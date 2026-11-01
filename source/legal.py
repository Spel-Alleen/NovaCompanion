"""Local article retrieval and source-bound, reviewed answers for game law."""
import hashlib
import math
import re
from collections import Counter

STOP=set('скажи расскажи пожалуйста ли я мы вы он она они это этот эта как что где когда почему какой какая какие можно могу может могут находиться находится находился быть если для про при или либо чтобы мне меня мой твой все всё еще уже есть надо нужно хочу знать вопрос закон законка статье статья статьи соответствии отношении является также'.split())

def norm(s): return re.sub(r'\s+',' ',s.replace('\u200b','').replace('\xa0',' ')).strip()
def fingerprint(s): return hashlib.sha256(norm(s).encode('utf-8')).hexdigest()
def stem(word):
    word=word.lower().replace('ё','е')
    if len(word)<=3: return word
    return re.sub(r'(иями|ями|ами|ого|его|ому|ему|ыми|ими|ая|яя|ое|ее|ые|ие|ий|ый|ой|ую|юю|ия|ью|ию|ям|ам|ах|ях|ом|ем|ов|ев|ей|а|я|ы|и|у|ю|е|о|ь)$','',word)
def tokens(s): return [stem(t) for t in re.findall(r'[а-яёa-z0-9]+',s.lower()) if len(t)>2 and t not in STOP]

ARTICLE=re.compile(r'^(?:Статья\s+)?(\d{1,2}(?:\.\d{1,2}){1,4})\.?\s*(.*)$',re.I)
HEADING=re.compile(r'^(?:Глава\s+|(?:\d+\s+)?Статья[.\s]|Правила посещения|Закон о правилах)',re.I)

def article_units(body):
    """Respect HTML paragraph boundaries, keep exceptions with their article."""
    lines=[norm(l) for l in body.splitlines() if norm(l)]
    units=[]; current=[]; ref=''; section=''; parent=''; ordinal=0
    def flush():
        nonlocal ordinal
        if not current: return
        text='\n'.join(current).strip()
        if len(text)>25 or (ref and len(text)>5):
            units.append({'reference':ref or parent or 'Раздел без номера','section':section,'heading':parent,'body':text,'ordinal':ordinal})
            ordinal+=1
    for line in lines:
        match=ARTICLE.match(line)
        if match:
            flush(); current=[line]; ref=match.group(1)
        elif HEADING.match(line):
            flush(); current=[]; ref=''
            if line.lower().startswith(('глава','правила посещения','закон о правилах')): section=line; parent=''
            else: parent=line
            current.append(line)
        else: current.append(line)
    flush()
    if not units and norm(body): units=[{'reference':'Текст документа','section':'','body':norm(body),'ordinal':0}]
    # Legacy imports without paragraph boundaries still remain searchable.
    expanded=[]
    for unit in units:
        if len(unit['body'])<=6500: expanded.append(unit); continue
        text=unit['body']; start=0
        while start<len(text):
            end=min(start+4500,len(text))
            if end<len(text):
                boundary=text.rfind('. ',start+2500,end)
                if boundary>start: end=boundary+1
            expanded.append(dict(unit,body=text[start:end],ordinal=unit['ordinal']+start/100000))
            if end==len(text): break
            start=max(start+1,end-300)
    return expanded

def retrieve(documents,query,research=False,limit=None):
    direct=set(tokens(query)); expanded=set(direct)
    mask=any(t.startswith(('маск','балаклав')) for t in direct)
    if mask: expanded.update({'маск','балаклав','идентификац','орм'})
    all_units=[]
    for doc in documents:
        doc=dict(doc)
        for unit in article_units(doc['body']):
            meta=dict(doc,reference=unit['reference'],section=unit['section'],heading=unit.get('heading',''),ordinal=unit['ordinal'])
            ts=Counter(tokens(unit['body'])); all_units.append((meta,unit['body'],ts))
    if not query.strip(): return [(0,m,b) for m,b,_ in all_units][:limit or 20]
    if not direct: return []
    frequency=Counter()
    for _,_,ts in all_units:
        for term in ts: frequency[term]+=1
    n=max(1,len(all_units)); ranked=[]
    for meta,body,ts in all_units:
        score=0
        for term in expanded:
            count=sum(c for t,c in ts.items() if t==term or (len(term)>3 and t.startswith(term)))
            if count:
                idf=math.log(1+n/(1+frequency.get(term,0)))
                score+=(2.5 if term in direct else .6)*idf*(count/(count+1.2))
        if mask and research:
            title=meta['title'].lower(); ref=meta['reference']
            if ('уголовн' in title and ref=='10.12') or ('процессуальн' in title and ref in ('8.1.1','8.1.2','8.1.6')):
                score=max(score,4.5)
        if score: ranked.append((score,meta,body))
    ranked.sort(key=lambda v:v[0],reverse=True)
    limit=limit or (22 if research else 12)
    if not research: return ranked[:limit]
    # Diversify by document before filling remaining slots: local exceptions matter.
    chosen=[]; counts=Counter(); deferred=[]
    for item in ranked:
        key=item[1]['url']
        if counts[key]<3: chosen.append(item); counts[key]+=1
        else: deferred.append(item)
    chosen=(chosen+deferred)[:limit]
    # Follow explicitly cited provisions, rather than relying on query wording alone.
    refs=set()
    for _,_,body in chosen[:10]:
        refs.update(re.findall(r'(?:стать[еяию]|пункт[аеу]?)\s+(\d+(?:\.\d+){1,3})',body,re.I))
    used={(m['url'],m['ordinal']) for _,m,_ in chosen}
    for meta,body,_ in all_units:
        if meta['reference'] in refs and ('кодекс' in meta['title'].lower()) and (meta['url'],meta['ordinal']) not in used:
            if len(chosen)>=limit: break
            chosen.append((1.5,meta,body))
    return chosen

def source_label(meta):
    return meta['title']+' · '+meta.get('section','')+' · п. '+meta.get('reference','')

def evidence_pack(passages,question,budget=10000):
    selected=[]; left=budget
    for _,meta,body in passages:
        if left<250: break
        # Preserve full core provisions and explicitly label any excerpt boundary.
        text=body
        if len(text)>min(2500,left):
            terms=set(tokens(question)); lines=[norm(v) for v in body.splitlines() if norm(v)]
            relevant=[i for i,line in enumerate(lines) if terms.intersection(tokens(line))]
            if relevant:
                indexes=set()
                for i in relevant[:4]: indexes.update(range(max(0,i-1),min(len(lines),i+3)))
                text='\n'.join(lines[i] for i in sorted(indexes))
            if len(text)>min(2500,left):
                text=text[:min(2400,left-100)]+'\n[Фрагмент сокращён; полный текст в источнике.]'
        cost=len(text)+len(meta['title'])+100
        if selected and cost>left: continue
        selected.append((meta,text)); left-=cost
    return selected

def reviewed_answer(store,server,question):
    ts=set(tokens(question)); is_mask=any(t.startswith(('маск','балаклав')) for t in ts)
    if not is_mask: return None
    # The reviewed overview is suitable for broad mask questions. Concrete incidents
    # still go through research, which must account for their additional facts.
    generic={'носить','нос','ношени','ношен','надевать','надев','надеть','надет','ходить','ход','разрешен','запрещен','разреш','запрещ','снять','сня','лиц','обычн','гражданин','граждан','игрок','можн','нахожусь','нахождени','законн','услови','сотрудник','гос','требован','иметь','прав','говор','расскаж','объясн','мест','улиц','город'}
    remaining={t for t in ts if not t.startswith(('маск','балаклав')) and t not in generic}
    if remaining: return None
    row=store.db.execute('SELECT * FROM reviewed_answers WHERE server=? AND topic=?',(server,'mask_overview')).fetchone()
    if not row: return None
    import json
    for url,expected in json.loads(row['fingerprints']).items():
        doc=store.db.execute('SELECT body FROM laws WHERE server=? AND url=?',(server,url)).fetchone()
        if not doc or fingerprint(doc['body'])!=expected: return None
    return row['answer']

def procedure_answer(documents,server,question):
    """Assemble complete legal procedure sections, with their actual server numbering.

    This is an extractive answer, not a canned detention response. It locates headings
    in whichever server's documents were supplied and preserves all subordinate steps.
    """
    q=question.lower().replace('ё','е')
    actions=[term for term in ('задерж','арест','обыск','досмотр','допрос','освобожд') if term in q]
    procedural=any(term in q for term in ('порядок','процедур','последователь','алгоритм','обязатель','должен','должны','как провод','как правил','шаг','что нужно','что надо'))
    if not actions or not procedural: return None
    # Case-specific factual disputes need inference rather than a general checklist.
    if any(term in q for term in ('законно ли','имел ли право','мог ли','меня задержали','нарушил ли','в моей ситуации')): return None
    groups=[]
    for document in documents:
        doc=dict(document); bucket={}
        for unit in article_units(doc['body']):
            heading=unit.get('heading',''); h=heading.lower().replace('ё','е')
            if not heading: continue
            if any(action in h for action in actions):
                key=(heading,unit['section']); bucket.setdefault(key,[]).append(unit)
        for (heading,section),units in bucket.items():
            if not any(re.search(r'\d+\.\d+',u['reference']) for u in units): continue
            h=heading.lower()
            category=0 if any(t in h for t in ('основан','услови')) else 1 if any(t in h for t in ('процесс','порядок','процедур')) else 2 if any(t in h for t in ('прав','обязан','участник')) else 3 if any(t in h for t in ('срок','освобожд','прекращ')) else 2
            groups.append((category,doc,heading,section,units))
    if not groups: return None
    groups.sort(key=lambda g:(g[0],not ('процессуальн' in g[1]['title'].lower()),g[1]['title'],g[4][0]['ordinal']))
    # Include all relevant sections, not merely the highest-ranked three fragments.
    titles=['ОСНОВАНИЯ И УСЛОВИЯ','ПОСЛЕДОВАТЕЛЬНОСТЬ И ОБЯЗАТЕЛЬНЫЕ ДЕЙСТВИЯ','ПРАВА, ОБЯЗАННОСТИ И ОСОБЫЕ СЛУЧАИ','СРОКИ, ОСВОБОЖДЕНИЕ И ПРЕКРАЩЕНИЕ']
    parts=[f'{server.upper()} · ПОРЯДОК ДЕЙСТВИЙ ПО ДОКУМЕНТАМ',
        'Автоматическая сводка: ниже собраны полные подходящие разделы выбранного сервера, с исходными номерами пунктов. Это прямые нормы из библиотеки, без дополнения правилами другого сервера. Условия и исключения оставлены рядом с соответствующим действием.']
    sources=[]; seen={}; previous=None
    for category,doc,heading,section,units in groups:
        if doc['url'] not in seen: seen[doc['url']]=len(sources)+1; sources.append(doc)
        sid=seen[doc['url']]
        if category!=previous: parts.append(titles[category]); previous=category
        parts.append(f'{heading} · {doc["title"]} [{sid}]')
        text='\n\n'.join(u['body'] for u in units if u['body'].strip()!=heading.strip())
        parts.append(text)
    parts.append('ЧТО УТОЧНИТЬ ДЛЯ КОНКРЕТНОГО СЛУЧАЯ\nКто проводит действие, кто задержанный/участник, его статус и неприкосновенность, место и особый режим, наличие угрозы жизни. Специальные правила из приведённых разделов могут менять общий порядок. Само наличие пункта в кодексе не доказывает, что его фактические условия выполнены.')
    parts.append('ИСТОЧНИКИ\n'+'\n\n'.join(f'[{i+1}] {doc["title"]}\n{doc["url"]}\nКопия: {doc["stamp"]}' for i,doc in enumerate(sources)))
    return '\n\n'.join(parts)
