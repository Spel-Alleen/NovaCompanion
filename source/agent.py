"""Start a bundled CPU-only model on loopback, on demand, without a system service."""
from pathlib import Path
import atexit
import os
import subprocess
import sys
import threading
import time
import secrets
import socket
import ctypes
from ctypes import wintypes
import requests

_process=None
_lock=threading.Lock()
_key=secrets.token_urlsafe(32)
_base=None
_job=None

def own_process(process):
    """Windows closes the model when its owning app exits, including abrupt exit."""
    class Basic(ctypes.Structure):
        _fields_=[('process_time',ctypes.c_longlong),('job_time',ctypes.c_longlong),('flags',wintypes.DWORD),('min_working',ctypes.c_size_t),('max_working',ctypes.c_size_t),('active',wintypes.DWORD),('affinity',ctypes.c_size_t),('priority',wintypes.DWORD),('scheduling',wintypes.DWORD)]
    class Io(ctypes.Structure):
        _fields_=[(name,ctypes.c_ulonglong) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
    class Extended(ctypes.Structure):
        _fields_=[('basic',Basic),('io',Io),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.CreateJobObjectW.argtypes=[ctypes.c_void_p,wintypes.LPCWSTR];k.CreateJobObjectW.restype=wintypes.HANDLE
    k.SetInformationJobObject.argtypes=[wintypes.HANDLE,ctypes.c_int,ctypes.c_void_p,wintypes.DWORD];k.SetInformationJobObject.restype=wintypes.BOOL
    k.AssignProcessToJobObject.argtypes=[wintypes.HANDLE,wintypes.HANDLE];k.AssignProcessToJobObject.restype=wintypes.BOOL
    k.CloseHandle.argtypes=[wintypes.HANDLE];k.CloseHandle.restype=wintypes.BOOL
    job=k.CreateJobObjectW(None,None)
    if not job:raise ctypes.WinError(ctypes.get_last_error())
    info=Extended();info.basic.flags=0x2000 # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k.SetInformationJobObject(job,9,ctypes.byref(info),ctypes.sizeof(info)) or not k.AssignProcessToJobObject(job,wintypes.HANDLE(int(process._handle))):
        error=ctypes.get_last_error();k.CloseHandle(job);raise ctypes.WinError(error)
    return job

def close_job():
    global _job
    if _job:
        k=ctypes.WinDLL('kernel32',use_last_error=True);k.CloseHandle.argtypes=[wintypes.HANDLE];k.CloseHandle(wintypes.HANDLE(_job));_job=None

def private_endpoint():
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    return port,'http://127.0.0.1:'+str(port)

def folder():
    if getattr(sys,'frozen',False): return Path(sys.executable).parent/'agent'
    nearby=Path(__file__).resolve().parent.parent/'agent'
    if nearby.exists(): return nearby
    return Path(__file__).resolve().parents[2]/'outputs'/'Nova'/'agent'

def available():
    p=folder()
    return (p/'llama-server.exe').exists() and any(f.stat().st_size>500_000_000 for f in p.glob('*.gguf'))

def stop():
    global _process,_base
    if _process is not None and _process.poll() is None:
        _process.terminate()
        try: _process.wait(timeout=5)
        except subprocess.TimeoutExpired: _process.kill()
    _process=None;_base=None;close_job()

atexit.register(stop)

def ensure():
    global _process,_base,_job
    with _lock:
        if _process is None or _process.poll() is not None:
            close_job()
            p=folder(); model=next(p.glob('*.gguf'),None)
            if model is None: raise RuntimeError('Файл локальной модели не найден в папке agent.')
            log=open(Path(os.getenv('LOCALAPPDATA','.'))/'NovaCompanion'/'agent.log','a',encoding='utf-8')
            port,_base=private_endpoint()
            try:
                _process=subprocess.Popen([str(p/'llama-server.exe'),'-m',str(model),'--host','127.0.0.1','--port',str(port),'--api-key',_key,'-c','8192','-t','3','-np','1','--no-ui'],cwd=str(p),stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW|subprocess.BELOW_NORMAL_PRIORITY_CLASS)
                try:_job=own_process(_process)
                except Exception:
                    _process.terminate();_process.wait(timeout=5);_process=None;_base=None;raise
            finally:log.close()
        for _ in range(90):
            if _process.poll() is not None: raise RuntimeError('Не удалось запустить локальную модель. Подробности в agent.log.')
            try:
                # /health is public on llama.cpp: a healthy foreign server can
                # still reject our key. Check an authenticated route instead.
                status=requests.get(_base+'/v1/models',headers={'Authorization':'Bearer '+_key},timeout=1).status_code
                if status==200:return
                if status in (401,403):raise RuntimeError('Встроенная модель отклонила ключ этого запуска. Перезапусти помощник.')
            except requests.RequestException: pass
            time.sleep(.5)
        raise RuntimeError('Модель ещё загружается. Повтори вопрос через несколько секунд.')

def answer(messages):
    ensure()
    response=requests.post(_base+'/v1/chat/completions',headers={'Authorization':'Bearer '+_key},json={'messages':messages,'stream':False,'temperature':.1,'max_tokens':850},timeout=1200)
    response.raise_for_status()
    return response.json()['choices'][0]['message']['content']
