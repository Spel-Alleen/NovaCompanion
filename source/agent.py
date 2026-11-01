"""Start a bundled CPU-only model on loopback, on demand, without a system service."""
from pathlib import Path
import atexit
import os
import subprocess
import sys
import threading
import time
import secrets
import requests

_process=None
_lock=threading.Lock()
_key=secrets.token_urlsafe(32)

def folder():
    if getattr(sys,'frozen',False): return Path(sys.executable).parent/'agent'
    nearby=Path(__file__).resolve().parent.parent/'agent'
    if nearby.exists(): return nearby
    return Path(__file__).resolve().parents[2]/'outputs'/'Nova'/'agent'

def available():
    p=folder()
    return (p/'llama-server.exe').exists() and any(f.stat().st_size>500_000_000 for f in p.glob('*.gguf'))

def stop():
    global _process
    if _process is not None and _process.poll() is None:
        _process.terminate()
        try: _process.wait(timeout=5)
        except subprocess.TimeoutExpired: _process.kill()
    _process=None

atexit.register(stop)

def ensure():
    global _process
    with _lock:
        if _process is None or _process.poll() is not None:
            p=folder(); model=next(p.glob('*.gguf'),None)
            if model is None: raise RuntimeError('Файл локальной модели не найден в папке agent.')
            log=open(Path(os.getenv('LOCALAPPDATA','.'))/'NovaCompanion'/'agent.log','a',encoding='utf-8')
            _process=subprocess.Popen([str(p/'llama-server.exe'),'-m',str(model),'--host','127.0.0.1','--port','18765','--api-key',_key,'-c','8192','-t','3','-np','1','--no-ui'],cwd=str(p),stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS)
            log.close()
        for _ in range(90):
            if _process.poll() is not None: raise RuntimeError('Не удалось запустить локальную модель. Подробности в agent.log.')
            try:
                if requests.get('http://127.0.0.1:18765/health',headers={'Authorization':'Bearer '+_key},timeout=1).status_code==200: return
            except requests.RequestException: pass
            time.sleep(.5)
        raise RuntimeError('Модель ещё загружается. Повтори вопрос через несколько секунд.')

def answer(messages):
    ensure()
    response=requests.post('http://127.0.0.1:18765/v1/chat/completions',headers={'Authorization':'Bearer '+_key},json={'messages':messages,'stream':False,'temperature':.1,'max_tokens':850},timeout=1200)
    response.raise_for_status()
    return response.json()['choices'][0]['message']['content']
