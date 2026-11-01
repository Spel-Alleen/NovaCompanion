import datetime as dt
import json
import re
import sqlite3
from pathlib import Path

SERVERS = {'Mirage': ['https://forum.grand-rp.su/forums/960/', 'https://forum.grand-rp.su/forums/1093/'], 'New Era': ['https://forum.grand-rp.su/forums/1320/'], 'Horizon': ['https://forum.grand-rp.su/forums/1478/']}

def money(n):
    return f'{n:,.0f}'.replace(',', ' ') + ' $'

def now():
    return dt.datetime.now().astimezone().isoformat(timespec='seconds')

class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS readings(id INTEGER PRIMARY KEY,server TEXT,stamp TEXT,cash INTEGER,bank INTEGER,delta INTEGER,kind TEXT);
        CREATE TABLE IF NOT EXISTS notes(server TEXT PRIMARY KEY,body TEXT);
        CREATE TABLE IF NOT EXISTS laws(id INTEGER PRIMARY KEY,server TEXT,title TEXT,url TEXT,body TEXT,stamp TEXT,UNIQUE(server,url));
        CREATE TABLE IF NOT EXISTS reviewed_answers(server TEXT,topic TEXT,answer TEXT,fingerprints TEXT,stamp TEXT,PRIMARY KEY(server,topic));''')
        self.db.commit()

    def get(self, key, default=None):
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))
        self.db.commit()

    def last(self, server):
        return self.db.execute('SELECT * FROM readings WHERE server=? ORDER BY id DESC LIMIT 1', (server,)).fetchone()

    def record(self, server, cash, bank, baseline=False, stamp=None):
        old = self.last(server)
        delta = 0 if baseline or old is None else cash + bank - old['cash'] - old['bank']
        if old and old['cash'] == cash and old['bank'] == bank and not baseline:
            return 0
        self.db.execute('INSERT INTO readings(server,stamp,cash,bank,delta,kind) VALUES(?,?,?,?,?,?)', (server, stamp or now(), cash, bank, delta, 'baseline' if baseline or old is None else 'scan'))
        self.db.commit()
        return delta

    def history(self, server, limit=300):
        return self.db.execute('SELECT * FROM readings WHERE server=? ORDER BY id DESC LIMIT ?', (server, limit)).fetchall()

    def totals(self, server, date=None):
        today = date or dt.date.today()
        starts = [today, today-dt.timedelta(days=today.weekday()), today.replace(day=1)]
        totals = [0, 0, 0]
        for row in self.db.execute('SELECT stamp,delta FROM readings WHERE server=?', (server,)):
            day = dt.datetime.fromisoformat(row['stamp']).astimezone().date()
            for i, start in enumerate(starts):
                if start <= day <= today:
                    totals[i] += row['delta']
        return totals

    def days(self, server):
        today=dt.date.today()
        values={today-dt.timedelta(days=i):0 for i in range(13,-1,-1)}
        for row in self.db.execute('SELECT stamp,delta FROM readings WHERE server=?',(server,)):
            day=dt.datetime.fromisoformat(row['stamp']).astimezone().date()
            if day in values: values[day]+=row['delta']
        return list(values.items())

    def note(self, server):
        row=self.db.execute('SELECT body FROM notes WHERE server=?',(server,)).fetchone()
        return row[0] if row else ''

    def save_note(self, server, body):
        self.db.execute('INSERT OR REPLACE INTO notes VALUES (?,?)',(server,body)); self.db.commit()

    def laws(self, server):
        return self.db.execute('SELECT * FROM laws WHERE server=? ORDER BY title',(server,)).fetchall()

    def save_law(self, server, title, url, body):
        self.db.execute('INSERT INTO laws(server,title,url,body,stamp) VALUES(?,?,?,?,?) ON CONFLICT(server,url) DO UPDATE SET title=excluded.title,body=excluded.body,stamp=excluded.stamp',(server,title,url,body,now())); self.db.commit()

    def search(self, server, query, research=False):
        from legal import retrieve
        return retrieve(self.laws(server),query,research)

def parse_lines(lines):
    """Find an aligned vertical cash/bank pair; never infer a missing row as zero."""
    candidates=[]
    for text,x,y,h in lines:
        text=text.strip()
        if ':' in text or '/' in text: continue
        match=re.fullmatch(r'[^\d]{0,5}([\d][\d . ,\u00a0]*)(?:\s*\$)?',text)
        if not match: continue
        raw=match.group(1).strip()
        if not re.fullmatch(r'\d{1,3}(?:[.,\s]\d{3})+|\d{1,10}',raw): continue
        n=int(re.sub(r'\D','',raw))
        if n>9_999_999_999: continue
        candidates.append((n,x,y,h,'$' in text))
    pairs=[]
    for a in candidates:
        for b in candidates:
            gap=b[2]-a[2]
            if max(a[3],b[3])*.65 <= gap <= max(a[3],b[3])*2.8 and abs(a[1]-b[1])<max(a[3],b[3])*2:
                # Currency symbols or thousands formatting are required for discovery.
                if not (a[4] or b[4] or (a[0]>=1000 and b[0]>=1000)): continue
                pairs.append((int(a[4])+int(b[4]),a,b))
    if not pairs: return None
    _,a,b=max(pairs,key=lambda p:(p[0],p[1][2]))
    return a[0],b[0]
