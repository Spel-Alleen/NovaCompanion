"""Signed GitHub Releases updates for the portable app and shared law libraries."""
import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
import zipfile
import requests

APP_ID='nova-companion'
DEFAULT_REPO='Spel-Alleen/NovaCompanion'
MAX_ARCHIVE=64*1024*1024
MAX_EXPANDED=128*1024*1024

def root_dir(): return Path(__file__).resolve().parent.parent
def data_dir(): return Path(os.environ.get('NOVA_DATA_DIR',str(Path(os.environ.get('LOCALAPPDATA','.'))/'NovaCompanion')))
def version(root=None):
    path=(Path(root) if root else root_dir())/'source'/'version.json'
    return json.loads(path.read_text(encoding='utf-8'))['version'] if path.exists() else '0.1.0'
def version_tuple(v):
    if not re.fullmatch(r'\d+\.\d+\.\d+',v): raise ValueError('Версия должна иметь вид 0.2.0.')
    return tuple(map(int,v.split('.')))
def canonical(value): return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')
def digest(blob): return hashlib.sha256(blob).hexdigest()
def trust_config(root=None): return json.loads(((Path(root) if root else root_dir())/'source'/'release-trust.json').read_text(encoding='utf-8'))

def verify_manifest(signed,public_key,repo,current_version):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    payload=signed['payload']
    Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key,validate=True)).verify(base64.b64decode(signed['signature'],validate=True),canonical(payload))
    if payload.get('app_id')!=APP_ID or payload.get('repo')!=repo: raise ValueError('Обновление выпущено для другого приложения или репозитория.')
    if version_tuple(payload['version'])<=version_tuple(current_version): raise ValueError('Эта версия уже установлена или старее текущей.')
    if not re.fullmatch(r'[0-9a-f]{64}',payload.get('sha256','')): raise ValueError('Неверный контрольный код обновления.')
    if not isinstance(payload.get('size'),int) or not 0<payload['size']<=MAX_ARCHIVE: raise ValueError('Недопустимый размер обновления.')
    if not re.fullmatch(r'Nova-update-\d+\.\d+\.\d+\.zip',payload.get('asset','')): raise ValueError('Неизвестный файл обновления.')
    return payload

def _github_asset(url,repo):
    prefix='https://github.com/'+repo+'/releases/download/'
    if not isinstance(url,str) or not url.startswith(prefix): raise ValueError('Ссылка обновления не принадлежит выбранному GitHub Releases.')
    return url

def check_release(root=None,current_version=None):
    config=trust_config(root); repo=config['repo']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo): raise ValueError('Неверный адрес репозитория.')
    r=requests.get(f'https://api.github.com/repos/{repo}/releases/latest',headers={'Accept':'application/vnd.github+json'},timeout=20)
    if r.status_code==404: return {'available':False,'message':'Первый публичный выпуск ещё не опубликован.'}
    r.raise_for_status(); release=r.json()
    assets={a['name']:a for a in release.get('assets',[])}
    if 'manifest.json' not in assets: return {'available':False,'message':'В выпуске нет файла обновления для Nova Companion.'}
    manifest_url=_github_asset(assets['manifest.json']['browser_download_url'],repo)
    response=requests.get(manifest_url,timeout=20); response.raise_for_status()
    if len(response.content)>256*1024: raise ValueError('Слишком большой файл описания обновления.')
    signed=response.json()
    # Authenticate even a same-version manifest; never trust version metadata first.
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    Ed25519PublicKey.from_public_bytes(base64.b64decode(config['public_key'])).verify(base64.b64decode(signed['signature']),canonical(signed['payload']))
    if signed['payload'].get('app_id')!=APP_ID or signed['payload'].get('repo')!=repo: raise ValueError('Неверный выпуск.')
    current=current_version or version(root)
    if version_tuple(signed['payload']['version'])<=version_tuple(current): return {'available':False,'message':'Установлена последняя опубликованная версия.'}
    payload=verify_manifest(signed,config['public_key'],repo,current)
    if payload['asset'] not in assets: raise ValueError('Архив обновления отсутствует в выпуске.')
    return {'available':True,'signed':signed,'payload':payload,'url':_github_asset(assets[payload['asset']]['browser_download_url'],repo),'message':'Доступна версия '+payload['version']}

def allowed_path(name):
    if '\\' in name or ':' in name or name.startswith('/') or '\x00' in name: return False
    path=PurePosixPath(name)
    if not path.parts or any(p in ('..','.') for p in path.parts): return False
    if name=='NovaCompanion.exe': return True
    if len(path.parts)!=2: return False
    if path.parts[0]=='source': return path.suffix.lower() in ('.py','.json','.ico','.txt','.cs','.ps1')
    if path.parts[0]=='library': return path.suffix.lower()=='.json'
    return False

def read_archive(path,payload):
    path=Path(path)
    if path.stat().st_size!=payload['size'] or digest(path.read_bytes())!=payload['sha256']: raise ValueError('Архив повреждён или не соответствует подписи автора.')
    files={}; total=0; seen=set()
    with zipfile.ZipFile(path) as archive:
        for entry in archive.infolist():
            if entry.is_dir(): continue
            if not allowed_path(entry.filename): raise ValueError('Недопустимый путь в обновлении: '+entry.filename)
            key=entry.filename.casefold()
            if key in seen or ((entry.external_attr>>16)&0o170000)==0o120000: raise ValueError('Повторный путь или ссылка в архиве.')
            seen.add(key); total+=entry.file_size
            if total>MAX_EXPANDED or entry.file_size>32*1024*1024: raise ValueError('Слишком большой распакованный пакет.')
            files[entry.filename]=archive.read(entry)
    if not {'source/app.py','source/version.json','source/release-trust.json'}<=files.keys(): raise ValueError('Неполный пакет приложения.')
    if json.loads(files['source/version.json'])['version']!=payload['version']: raise ValueError('Версия архива не совпадает с описанием.')
    return files

def download_release(result,progress):
    target=data_dir()/'updates'/uuid.uuid4().hex; target.mkdir(parents=True)
    archive=target/'update.zip'; count=0
    with requests.get(result['url'],stream=True,timeout=40) as response:
        response.raise_for_status()
        with archive.open('wb') as stream:
            for block in response.iter_content(128*1024):
                count+=len(block)
                if count>MAX_ARCHIVE: raise ValueError('Превышен размер обновления.')
                stream.write(block); progress(f'Загружено {count//1024} КБ')
    read_archive(archive,result['payload'])
    (target/'manifest.json').write_text(json.dumps(result['signed'],ensure_ascii=False),encoding='utf-8')
    return str(target)

def apply_shared_library(connection,pack,release_version):
    from core import SERVERS,now
    server=pack['server']
    if server not in SERVERS: raise ValueError('Неизвестный сервер в пакете законки.')
    docs=pack.get('documents',[]); urls=[d['url'] for d in docs]
    if len(urls)!=len(set(urls)) or not docs: raise ValueError('Пустая библиотека или повторные документы.')
    connection.execute('CREATE TABLE IF NOT EXISTS distributed_laws(server TEXT,url TEXT,version TEXT,PRIMARY KEY(server,url))')
    old=[r[0] for r in connection.execute('SELECT url FROM distributed_laws WHERE server=?',(server,))]
    for url in old:
        if url not in urls: connection.execute('DELETE FROM laws WHERE server=? AND url=?',(server,url))
    for doc in docs:
        if not all(isinstance(doc.get(k),str) and doc[k] for k in ('title','url','body')): raise ValueError('Неполный документ.')
        connection.execute('INSERT INTO laws(server,title,url,body,stamp) VALUES(?,?,?,?,?) ON CONFLICT(server,url) DO UPDATE SET title=excluded.title,body=excluded.body,stamp=excluded.stamp',(server,doc['title'],doc['url'],doc['body'],now()))
    connection.execute('DELETE FROM distributed_laws WHERE server=?',(server,))
    connection.executemany('INSERT INTO distributed_laws VALUES(?,?,?)',[(server,url,release_version) for url in urls])
    connection.execute('DELETE FROM reviewed_answers WHERE server=?',(server,))
    for row in pack.get('reviewed',[]):
        connection.execute('INSERT INTO reviewed_answers VALUES(?,?,?,?,?)',(server,row['topic'],row['answer'],json.dumps(row['fingerprints']),now()))

def apply_update(root,staged,database,current_version=None):
    root=Path(root).resolve(); staged=Path(staged).resolve(); database=Path(database)
    config=trust_config(root); signed=json.loads((staged/'manifest.json').read_text(encoding='utf-8'))
    payload=verify_manifest(signed,config['public_key'],config['repo'],current_version or version(root))
    files=read_archive(staged/'update.zip',payload)
    backup=staged/'rollback'; backup.mkdir(exist_ok=True); existed={}; applied=[]
    connection=sqlite3.connect(str(database))
    target=sqlite3.connect(str(staged/'database-backup.sqlite3'))
    try: connection.backup(target)
    finally: target.close()
    try:
        connection.execute('BEGIN IMMEDIATE')
        for name,blob in files.items():
            dest=(root/name).resolve()
            if not dest.is_relative_to(root): raise ValueError('Путь за пределами приложения.')
            existed[name]=dest.exists()
            if dest.exists():
                saved=backup/name; saved.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(dest,saved)
            dest.parent.mkdir(parents=True,exist_ok=True)
            tmp=dest.with_name(dest.name+'.nova-new'); tmp.write_bytes(blob)
            for attempt in range(30):
                try: os.replace(tmp,dest); break
                except PermissionError:
                    if attempt==29: raise
                    time.sleep(.2)
            applied.append(name)
        for name,blob in files.items():
            if name.startswith('library/'): apply_shared_library(connection,json.loads(blob),payload['version'])
        connection.commit()
    except Exception:
        connection.rollback()
        for name in reversed(applied):
            dest=root/name
            if existed[name]: shutil.copy2(backup/name,dest)
            elif dest.exists(): dest.unlink()
        raise
    finally: connection.close()
    return payload['version']

def run_installer(root,staged,pid):
    root=Path(root); staged=Path(staged); log=staged/'install-result.json'
    try:
        user=ctypes.windll.kernel32; user.OpenProcess.restype=ctypes.c_void_p
        handle=user.OpenProcess(0x00100000,False,int(pid))
        if handle:
            try:
                if user.WaitForSingleObject(ctypes.c_void_p(handle),60000)!=0: raise RuntimeError('Программа ещё не завершилась.')
            finally: user.CloseHandle(ctypes.c_void_p(handle))
        installed=apply_update(root,staged,data_dir()/'companion.sqlite3')
        log.write_text(json.dumps({'ok':True,'version':installed}),encoding='utf-8')
    except Exception as exc:
        log.write_text(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False),encoding='utf-8')
        ctypes.windll.user32.MessageBoxW(None,'Обновление не установлено: '+str(exc)+'\nПредыдущая версия сохранена.','Nova Companion',0x10)
    subprocess.Popen([str(root/'NovaCompanion.exe')],cwd=str(root),creationflags=subprocess.CREATE_NO_WINDOW)

def launch_installer(staged):
    root=root_dir(); staged=Path(staged)
    helper=staged/'install.py'
    helper.write_text('import sys\nsys.path.insert(0,sys.argv[1]+"/source")\nfrom updates import run_installer\nrun_installer(sys.argv[1],sys.argv[2],sys.argv[3])\n',encoding='utf-8')
    subprocess.Popen([str(root/'runtime'/'pythonw.exe'),str(helper),str(root),str(staged),str(os.getpid())],cwd=str(root),creationflags=subprocess.CREATE_NO_WINDOW)
