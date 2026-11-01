"""Conversation state, concise legal answers, and source-based clarification."""
import re
from legal import reviewed_answer,procedure_answer,article_units,norm

def clean_text(text):
    lines=[]
    for line in text.splitlines():
        value=line.strip()
        plain=value.strip('*# ')
        if re.match(r'^[A-ZА-ЯЁ ]+\s*·\s*(?:МАСКА|ПОРЯДОК)',plain,re.I): continue
        if re.match(r'^(?:Проверенный разбор|Автоматическая сводка|Этот разбор автоматически|Каждая ссылка ниже)',plain,re.I): continue
        value=re.sub(r'Когда нельзя\s+и\s+где есть исключения[:.]?','Когда нельзя',value,flags=re.I)
        if value.startswith('• Служебные исключения:') and next((v for v in reversed(lines) if v.strip()),'')!='Где есть исключения': lines.extend(['','Где есть исключения'])
        if len(value)>4 and value.isupper() and any(c.isalpha() for c in value): value=value.capitalize()
        lines.append(value)
    return re.sub(r'\n{3,}','\n\n','\n'.join(lines)).strip()

def effective_query(question,history):
    q=question.strip()
    topics=('маск','балаклав','задерж','арест','обыск','досмотр','оруж','адвокат','допрос')
    if any(t in q.lower() for t in topics): return q
    previous=[m['content'] for m in history if m['role']=='user']
    return '\n'.join(previous[-2:]+[q]) if previous else q

def active_topic(question,history):
    topic=None
    for line in [m['content'] for m in history if m['role']=='user']+[question]:
        q=line.lower().replace('ё','е')
        if any(t in q for t in ('маск','балаклав')): topic='mask'
        elif re.search(r'оруж|обыск|досмотр|адвокат|допрос|парков|скорост|штраф',q): topic='other'
        elif re.search(r'задерж|арест',q):
            factual=bool(re.fullmatch(r'(?:да[, ]*)?(?:я|меня)\s+(?:не\s+)?(?:задержан[аы]?|задержали)[.! ]*',q.strip()))
            hypothetical=bool(re.fullmatch(r'(?:а\s+)?при\s+задержании[?!. ]*',q.strip()))
            if not factual and not hypothetical: topic='detention'
    return topic

def sources_from_documents(documents,ids):
    result=[]
    for number,thread,reference in ids:
        doc=next((dict(d) for d in documents if '/'+thread+'/' in d['url']),None)
        if doc: result.append({'number':number,'title':doc['title'],'reference':reference,'url':doc['url']})
    return result

def mask_answer(store,server,question,history):
    # Every fast mask answer remains bound to the source fingerprints of the reviewed
    # analysis. A different server or changed document falls back to fresh research.
    if active_topic(question,history)!='mask': return None
    context=effective_query(question,history).lower().replace('ё','е')
    users='\n'.join(m['content'] for m in history[-10:] if m['role']=='user')+'\n'+question
    lower=users.lower().replace('ё','е')
    if not any(t in (context+' '+lower) for t in ('маск','балаклав')): return None
    reviewed=reviewed_answer(store,server,'маска')
    if not reviewed: return None
    docs=store.laws(server)
    sources=sources_from_documents(docs,[(1,'410587','п. 2.6'),(2,'410602','п. 2.2 соответствующего раздела'),(3,'410586','п. 8.1.6'),(4,'410608','п. 5.2'),(5,'410598','ст. 12'),(6,'410588','ст. 10.12')])
    q=question.lower().replace('ё','е'); answer=None
    if re.search(r'паспорт|показать\s+документ|удостоверени|телефон|лицензи',q): return None
    if not any(t in q for t in ('маск','балаклав')) and re.search(r'порядок|процедур|оруж|обыск|досмотр|адвокат|допрос|штраф|парков|скорост',q): return None
    # Only user statements establish facts. An earlier assistant's conditional example
    # does not establish that an ORM or a detention actually exists.
    fact_lines=[m['content'].lower().replace('ё','е') for m in history[-10:] if m['role']=='user']+[q]
    place=None; site=None; orm=None; detention=None
    for line in fact_lines:
        located=re.search(r'(?:в\s+холле|внутри|на\s+территории)\s+(lspd|лспд|sahp|fib|фиб|капитол\w*)',line)
        if located: place='restricted'; site=located.group(1)
        elif re.search(r'в\s+холле',line): place='unknown_hall'; site=None
        elif re.search(r'(?:я\s+)?(?:стою|был|нахожусь|это)?\s*на\s+(?:обычной\s+)?улице',line): place='street'; site=None
        if re.search(r'орм\s+(?:нет|не\s+(?:проводят|проводятся))|без\s+орм',line): orm=False
        elif re.search(r'орм\s+(?:идут|проводят|проводятся)|проводят\s+орм',line): orm=True
        if re.search(r'не\s+задержан|меня\s+не\s+задерж',line): detention=False
        elif re.search(r'я\s+задержан|меня\s+задержали',line): detention=True
    scenario_orm=bool(re.search(r'при\s+орм|если.{0,45}орм|во\s+время\s+орм',q))
    scenario_detention='при задержании' in q
    demand=any(t in context for t in ('требов','лспдш','сотрудник','снять','запретить','не надев')) or place is not None or orm is not None or detention is not None
    if 'исключени' in q:
        answer='Где есть исключения\n• В Капитолии обычному посетителю можно находиться в маске при одобрении Губернатора, Вице-Губернатора или Генерального прокурора — п. 2.2 правил посещения. [2]\n• Пункт 2.6 АК отдельно перечисляет служебные спецоперации, отделы быстрого реагирования, конспирацию первых лиц и медмаски EMS при исполнении. Это не общее разрешение любому посетителю. [1]\n• В Центральной больнице и рядом с ней исключение для макияжа/грима не означает разрешения любой маски. [1]\n• Для служебной формы госслужащих в специальных зонах действуют отдельные исключения. [4][5]\n\nКакое место и какой у тебя статус — посетитель или сотрудник при исполнении?'
    elif re.search(r'какой.{0,20}(?:закон|пункт)|какая.{0,20}стать|ссылаешь',q):
        answer='Основная норма — п. 2.6 Административного кодекса: ношение масок, снятие при ОРМ и предел требования не надевать её дальше. [1]\n\nДля холла LSPD также применяется п. 2.2 соответствующего раздела правил посещения госучреждений. Для снятия маски при задержании — п. 8.1.6 Процессуального кодекса. [2][3]'
    elif re.search(r'подробнее|почему|зачем',q) and not any(t in q for t in ('требов','снять','маск')): return None
    elif re.search(r'не\s+надевать|никогда|больше\s+не\s+(?:носить|надевать)',q):
        answer='Нет, требовать «не надевать маску в последующем» нельзя: это прямо указано в п. 2.6 Административного кодекса. [1]\n\nПри этом запрет маски в конкретном госучреждении или специальной зоне продолжает действовать. Снять для идентификации и запретить носить вообще — разные требования.'
    elif demand and (place=='restricted' or detention is True or orm is True or scenario_orm or scenario_detention):
        if place=='restricted': answer='Да, в холле LSPD обычному посетителю запрещено находиться в маске. Основание — п. 2.2 раздела о посещении Центральной полиции и п. 2.6 Административного кодекса. [1][2]\n\nЭто относится к указанной территории. Постоянно запрещать тебе носить маску после выхода из неё только на этом основании нельзя. Для засекреченных госслужащих предусмотрено отдельное исключение.' if site in ('lspd','лспд') else 'Для обычного посетителя маска в указанном госучреждении запрещена: учитываются п. 2.6 Административного кодекса и соответствующий раздел правил посещения. [1][2]\n\nТребование относится к режиму территории. Есть служебные исключения — уточни, если ты находишься там как госслужащий.'
        elif detention is True or scenario_detention: answer='Да, при задержании сотрудник предлагает снять маску для идентификации. Если задержанный отказывается добровольно, п. 8.1.6 Процессуального кодекса допускает её принудительное снятие. [3]\n\nЭто правило процедуры задержания; оно само по себе не доказывает законность оснований твоего задержания.'
        else: answer='Да, если это представитель правоохранительных органов и маску требуют снять для идентификации при ОРМ. Гражданин обязан выполнить такое требование — п. 2.6 Административного кодекса. [1]\n\nТребование «больше не надевай её вообще» тот же пункт прямо называет незаконным.'
    elif demand:
        answer='Снять маску для идентификации при ОРМ — да. Запретить носить её в дальнейшем вообще — нет. Оба положения содержатся в п. 2.6 Административного кодекса. [1]\n\nЧтобы ответить именно про твой случай: ты на обычной улице или на территории LSPD? Ты задержан, и проводятся ли ОРМ?'
        if place=='street': answer='Одного факта «сотрудник LSPD потребовал снять маску на улице» недостаточно для ответа да или нет. По п. 2.6 Административного кодекса такое требование предусмотрено при ОРМ для идентификации; при задержании действует п. 8.1.6 ПК. [1][3]\n\nТы задержан? Проводятся ли ОРМ и что именно сотрудник потребовал?'
        if place=='unknown_hall': answer='Холл какого здания — LSPD, больницы, частного помещения или другого учреждения? И ты обычный посетитель или сотрудник при исполнении?\n\nОт этого зависят запрет маски и исключения. В п. 2.6 Административного кодекса и правилах посещения перечислены конкретные случаи. [1][2]'
        if place=='street' and orm is False and detention is False: answer='По указанным тобой обстоятельствам основание для такого требования не установлено: ты на обычной улице, не задержан и, по твоим словам, ОРМ нет. Пункт 2.6 АК связывает снятие для идентификации с ОРМ, а п. 8.1.6 ПК — с задержанием. [1][3]\n\nЕсть ли другое основание, которое назвал сотрудник, или особый режим территории? Без этого я не буду утверждать, что любое его требование незаконно.'
    if answer is None:
        answer='Обычно да — на обычной улице, если нет специальных ограничений. Пункт 2.6 Административного кодекса не устанавливает общего запрета масок повсюду. [1]\n\nКогда нельзя\n• В перечисленных в п. 2.6 госорганизациях и на прилегающих территориях.\n• В холлах LSPD, SAHP и FIB по правилам посещения.\n• В жёлтой зоне Форта и в специальных зонах исправительной колонии действуют отдельные ограничения. [1][2][4][5]\n\nГде есть исключения\n• В Капитолии — при одобрении указанных в правилах должностных лиц.\n• Для отдельных служебных случаев, спецопераций и сотрудников EMS при исполнении.\n• Исключение для грима в больнице не означает разрешения любой маски обычному посетителю. [1][2]\n\nЧто могут требовать\nПри ОРМ правоохранители вправе потребовать снять маску для идентификации. При задержании действует отдельный порядок. Запрет «не надевать маску в последующем» п. 2.6 АК прямо называет незаконным. [1][3]\n\nТы спрашиваешь в целом или про конкретное место и требование сотрудника?'
    return {'answer':clean_text(answer),'details':clean_text(reviewed),'sources':sources}

def procedure_summary(documents,server,question):
    full=procedure_answer(documents,server,question)
    if not full: return None
    actions=[t for t in ('задерж','арест','обыск','досмотр','допрос','освобожд') if t in question.lower()]
    candidates=[]
    for row in documents:
        doc=dict(row); units=article_units(doc['body'])
        for heading in dict.fromkeys(u.get('heading','') for u in units):
            h=heading.lower()
            if any(t in h for t in actions) and any(t in h for t in ('процесс','порядок','процедур')):
                subset=[u for u in units if u.get('heading')==heading and re.fullmatch(r'\d+(?:\.\d+)+',u['reference'])]
                if subset: candidates.append((bool(re.search(r'гос|военно|неприкоснов',h)),not ('процессуальн' in doc['title'].lower()),doc,heading,subset))
    if not candidates: return {'answer':'Подходящие нормы собраны в подробностях. Уточни, какой этап процедуры тебя интересует.','details':clean_text(full),'sources':[]}
    _,_,doc,heading,units=sorted(candidates,key=lambda x:(x[0],x[1]))[0]
    parents=[u for u in units if re.search(r'следующ.{0,20}последователь|следующ.{0,20}поряд',u['body'],re.I)]
    parent=parents[0] if parents else None
    depth=parent['reference'].count('.')+1 if parent else min(u['reference'].count('.') for u in units)
    steps=[u for u in units if u['reference'].count('.')==depth and (not parent or u['reference'].startswith(parent['reference']+'.'))]
    lines=['Кратко порядок такой:']
    for u in steps[:12]:
        body=re.sub(r'^'+re.escape(u['reference'])+r'\.?\s*','',u['body'])
        sentence=re.split(r'(?<=[.!?])\s+(?=[А-ЯЁ])',body)[0]
        if len(sentence)>240: sentence=sentence[:235].rsplit(' ',1)[0]+'…'
        lines.append('• '+norm(sentence)+f' — п. {u["reference"]}. [1]')
    lines.append('\nУсловия и исключения')
    if parent: lines.append(norm(parent['body']))
    lines.append('Для госслужащего, лица с неприкосновенностью или при угрозе жизни порядок может отличаться. Полные условия, права и сроки — в раскрываемых статьях ниже.\n\nТы спрашиваешь общий порядок или разбираем конкретное задержание?')
    return {'answer':clean_text('\n'.join(lines)),'details':clean_text(full),'sources':[{'number':1,'title':doc['title'],'reference':heading,'url':doc['url']}]}

def answer_turn(store,server,question,history):
    if re.fullmatch(r'\s*(?:спасибо(?: большое)?|благодарю|понял[а]?|ясно|хорошо|ок|ага)[.! ]*',question,re.I):
        return {'answer':'Пожалуйста. Если появится конкретная ситуация, расскажи — разберём по статьям.','details':'','sources':[]}
    response=mask_answer(store,server,question,history)
    if response: return response
    expanded=effective_query(question,history)
    target=expanded if re.search(r'порядок|процедур|последователь|алгоритм',question.lower()) else question
    response=procedure_summary(store.laws(server),server,target)
    if response: return response
    return None
