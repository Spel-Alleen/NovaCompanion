"""Author-only release builder. The signing key lives outside the distributed app."""
import argparse
import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
from updates import APP_ID,DEFAULT_REPO,canonical,digest,version,version_tuple,root_dir

AUTHOR=Path(os.environ.get('LOCALAPPDATA','.'))/'NovaCompanionPublisher'
KEY=AUTHOR/'release-key.dpapi'

class Blob(ctypes.Structure): _fields_=[('length',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
def protect(data,decode=False):
    buf=ctypes.create_string_buffer(data); source=Blob(len(data),ctypes.cast(buf,ctypes.POINTER(ctypes.c_ubyte))); target=Blob()
    if decode: ok=ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target))
    else: ok=ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source),'Nova Companion release signing key',None,None,None,1,ctypes.byref(target))
    if not ok: raise ctypes.WinError()
    try: return ctypes.string_at(target.data,target.length)
    finally: ctypes.windll.kernel32.LocalFree(target.data)

def initialize_author(root=None):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    root=Path(root or root_dir()); AUTHOR.mkdir(parents=True,exist_ok=True)
    if not KEY.exists():
        key=Ed25519PrivateKey.generate()
        raw=key.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption())
        KEY.write_bytes(protect(raw))
    key=Ed25519PrivateKey.from_private_bytes(protect(KEY.read_bytes(),True))
    public=base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode()
    config=root/'source'/'release-trust.json'
    if config.exists() and json.loads(config.read_text(encoding='utf-8'))['public_key']!=public: raise ValueError('Ключ автора не совпадает с ключом этой поставки.')
    config.write_text(json.dumps({'repo':DEFAULT_REPO,'public_key':public},indent=2),encoding='utf-8')
    return key

def build_release(release_version,notes,database,destination,root=None):
    from core import Store,SERVERS
    from legal import fingerprint
    root=Path(root or root_dir()); key=initialize_author(root)
    if version_tuple(release_version)<=version_tuple(version(root)): raise ValueError('Новая версия должна быть выше установленной.')
    destination=Path(destination)/('v'+release_version); destination.mkdir(parents=True,exist_ok=True)
    archive=destination/f'Nova-update-{release_version}.zip'; source=Store(database)
    libraries=[]
    for server in SERVERS:
        docs=[dict(d) for d in source.laws(server)]
        if not docs: continue
        for doc in docs:
            for field in ('id','server','stamp'): doc.pop(field,None)
        expected={d['url']:fingerprint(d['body']) for d in docs}; reviewed=[]
        for row in source.db.execute('SELECT * FROM reviewed_answers WHERE server=?',(server,)):
            checks=json.loads(row['fingerprints'])
            if all(expected.get(url)==checksum for url,checksum in checks.items()): reviewed.append({'topic':row['topic'],'answer':row['answer'],'fingerprints':checks})
        libraries.append({'server':server,'documents':docs,'reviewed':reviewed})
    source.db.close()
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for path in sorted((root/'source').iterdir()):
            if not path.is_file() or path.suffix not in ('.py','.json','.ico','.txt','.cs','.ps1'): continue
            if path.name.startswith(('test_','verify_')) or path.name=='version.json': continue
            z.write(path,'source/'+path.name)
        z.writestr('source/version.json',json.dumps({'version':release_version}))
        for pack in libraries: z.writestr('library/'+pack['server']+'.json',json.dumps(pack,ensure_ascii=False))
    config=json.loads((root/'source'/'release-trust.json').read_text())
    payload={'app_id':APP_ID,'repo':config['repo'],'version':release_version,'asset':archive.name,'size':archive.stat().st_size,'sha256':digest(archive.read_bytes()),'notes':notes}
    signed={'payload':payload,'signature':base64.b64encode(key.sign(canonical(payload))).decode()}
    (destination/'manifest.json').write_text(json.dumps(signed,ensure_ascii=False,indent=2),encoding='utf-8')
    (destination/'Release-notes.txt').write_text(notes,encoding='utf-8')
    return destination

def gh_path():
    installed=shutil.which('gh')
    bundled=AUTHOR/'gh'/'bin'/'gh.exe'
    return installed or (str(bundled) if bundled.exists() else None)

def publish(directory):
    gh=gh_path()
    if not gh: raise RuntimeError('GitHub CLI не найден. Для публикации установи официальный GitHub CLI и войди в свой аккаунт.')
    directory=Path(directory); signed=json.loads((directory/'manifest.json').read_text(encoding='utf-8')); p=signed['payload']
    status=subprocess.run([gh,'auth','status','--hostname','github.com'],capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
    if status.returncode: raise RuntimeError('Сначала нажми «Войти в GitHub» и заверши вход самостоятельно.')
    account=subprocess.run([gh,'api','user','--jq','.login'],capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
    if account.returncode or account.stdout.strip().lower()!=p['repo'].split('/')[0].lower(): raise RuntimeError('Для этого выпуска войди в GitHub как '+p['repo'].split('/')[0]+'.')
    info=subprocess.run([gh,'repo','view',p['repo'],'--json','visibility,defaultBranchRef'],capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
    if info.returncode:
        created=subprocess.run([gh,'repo','create',p['repo'],'--public','--add-readme','--description','Nova Companion — desktop companion for NOVA RP'],capture_output=True,text=True,encoding='utf-8',errors='replace',creationflags=subprocess.CREATE_NO_WINDOW)
        if created.returncode: raise RuntimeError(created.stderr.strip())
        info=subprocess.run([gh,'repo','view',p['repo'],'--json','visibility,defaultBranchRef'],capture_output=True,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
    if info.returncode: raise RuntimeError(info.stderr.strip())
    metadata=json.loads(info.stdout)
    if metadata.get('visibility')!='PUBLIC': raise RuntimeError('Для обновлений без входа у пользователей нужен публичный репозиторий. Видимость автоматически не менялась.')
    branch=(metadata.get('defaultBranchRef') or {}).get('name') or 'main'
    cmd=[gh,'release','create','v'+p['version'],str(directory/p['asset']),str(directory/'manifest.json'),'--repo',p['repo'],'--target',branch,'--title','Nova Companion '+p['version'],'--notes-file',str(directory/'Release-notes.txt')]
    result=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',errors='replace',creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode: raise RuntimeError(result.stderr.strip())
    initial=directory/'Nova-Portable.zip'
    if initial.exists():
        upload=subprocess.run([gh,'release','upload','v'+p['version'],str(initial),'--repo',p['repo']],capture_output=True,text=True,encoding='utf-8',errors='replace',creationflags=subprocess.CREATE_NO_WINDOW)
        if upload.returncode: return result.stdout.strip()+'\nОбновление опубликовано; полный архив не загружен: '+upload.stderr.strip()
    return result.stdout.strip()

def build_portable(destination,release_version,root=None):
    """Explicit distribution allowlist: no app-data database or author's files."""
    root=Path(root or root_dir()); destination=Path(destination)/'Nova-Portable.zip'
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED,compresslevel=1,allowZip64=True) as z:
        for dirname in ('runtime','source','agent','library','Licenses'):
            for path in sorted((root/dirname).rglob('*')):
                if not path.is_file() or '__pycache__' in path.parts: continue
                relative=path.relative_to(root).as_posix()
                if relative=='source/version.json': z.writestr('Nova/'+relative,json.dumps({'version':release_version})); continue
                z.write(path,'Nova/'+relative,compress_type=zipfile.ZIP_STORED if path.suffix.lower()=='.gguf' else zipfile.ZIP_DEFLATED)
        for name in ('NovaCompanion.exe','Начни здесь.md'):
            if (root/name).exists(): z.write(root/name,'Nova/'+name)
    return destination

def gui(release_dir=None):
    from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QLabel,QLineEdit,QTextEdit,QPushButton,QMessageBox,QFileDialog
    from app import STYLE,Job
    app=QApplication(sys.argv); app.setStyle('Fusion'); app.setStyleSheet(STYLE)
    jobs=[]
    class PublisherWindow(QWidget):
        def closeEvent(self,event):
            if jobs: QMessageBox.information(self,'Выпуск обновления','Дождись завершения сборки или публикации.'); event.ignore()
            else: event.accept()
    w=PublisherWindow(); w.setObjectName('root'); w.setWindowTitle('Nova Companion · Выпуск обновления'); w.resize(600,500); layout=QVBoxLayout(w)
    layout.addWidget(QLabel('Публикация изменений приложения и общей законки.\nЛичные заметки, деньги и настройки в пакет не входят.'))
    v=list(version_tuple(version())); v[2]+=1; field=QLineEdit('.'.join(map(str,v))); layout.addWidget(QLabel('Новая версия')); layout.addWidget(field)
    notes=QTextEdit(); notes.setPlaceholderText('Что изменилось для пользователей'); layout.addWidget(notes)
    state=QLabel('Репозиторий: '+DEFAULT_REPO); state.setWordWrap(True); layout.addWidget(state)
    build=QPushButton('Собрать подписанный пакет'); send=QPushButton('Опубликовать в GitHub Releases'); send.setEnabled(False); login=QPushButton('Войти в GitHub'); layout.addWidget(build); layout.addWidget(login); layout.addWidget(send)
    result_dir=[None]
    if release_dir and (Path(release_dir)/'manifest.json').exists():
        result_dir[0]=Path(release_dir); payload=json.loads((result_dir[0]/'manifest.json').read_text(encoding='utf-8'))['payload']; field.setText(payload['version']); notes.setPlainText(payload.get('notes','')); state.setText('Готовый выпуск: '+str(result_dir[0])); send.setEnabled(True)
    def job(fn,done):
        build.setEnabled(False); send.setEnabled(False); login.setEnabled(False)
        j=Job(lambda progress:fn()); jobs.append(j); j.done.connect(done); j.failed.connect(lambda s:QMessageBox.information(w,'Публикация',s))
        def finished():
            jobs.remove(j); build.setEnabled(True); send.setEnabled(result_dir[0] is not None); login.setEnabled(True)
        j.finished.connect(finished); j.start()
    def assemble():
        if not KEY.exists(): QMessageBox.information(w,'Авторский ключ','На этом компьютере нет ключа автора.'); return
        dest=QFileDialog.getExistingDirectory(w,'Куда сохранить выпуск')
        if not dest: return
        ver=field.text().strip(); body=notes.toPlainText().strip() or 'Обновление приложения и законки.'
        db=Path(os.environ.get('LOCALAPPDATA','.'))/'NovaCompanion'/'companion.sqlite3'
        def done(path): result_dir[0]=path; send.setEnabled(True); state.setText('Готовый выпуск: '+str(path))
        job(lambda:build_release(ver,body,db,dest),done)
    def auth():
        gh=gh_path()
        if not gh: QMessageBox.information(w,'GitHub','Нужен GitHub CLI с официального сайта cli.github.com.'); return
        subprocess.Popen([gh,'auth','login','--hostname','github.com','--git-protocol','https','--web'],creationflags=subprocess.CREATE_NEW_CONSOLE)
    def send_release():
        if result_dir[0]: state.setText('Публикую выпуск и архив программы. Это может занять несколько минут…'); job(lambda:publish(result_dir[0]),lambda url:state.setText('Опубликовано: '+url))
    build.clicked.connect(assemble); login.clicked.connect(auth); send.clicked.connect(send_release)
    w.show(); app.exec()

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--init-author',action='store_true'); parser.add_argument('--gui',action='store_true'); parser.add_argument('--release-dir'); args=parser.parse_args()
    if args.init_author: initialize_author(); print('Авторский ключ сохранён на этом компьютере. Открытый ключ добавлен в поставку.')
    else: gui(args.release_dir)
