"""Identify the active GTA/RAGE window without relying on its randomized title."""
import ctypes
from ctypes import wintypes
import os
import re
import time
import unicodedata

GAME_PROCESSES={'gta5.exe','gta5_enhanced.exe','gta5_legacy.exe'}
UI_PROCESSES={'ragemp_game_ui.exe'}
LOOKALIKES=str.maketrans({
    'А':'A','а':'a','В':'B','С':'C','с':'c','Е':'E','е':'e','Н':'H','К':'K','к':'k','М':'M','м':'m','О':'O','о':'o','Р':'P','р':'p','Т':'T','т':'t','Х':'X','х':'x','У':'Y','у':'y','І':'I','і':'i','Ј':'J','ј':'j','Ѕ':'S','ѕ':'s',
    'Α':'A','Β':'B','Ε':'E','Η':'H','Ι':'I','Κ':'K','Μ':'M','Ν':'N','Ο':'O','Ρ':'P','Τ':'T','Υ':'Y','Χ':'X','ο':'o','ι':'i','ϲ':'c'
})
def normalized_title(title):
    text=unicodedata.normalize('NFKC',title).translate(LOOKALIKES).casefold()
    return ''.join(ch for ch in text if ch.isascii() and ch.isalnum())

def is_game_title(title):
    text=normalized_title(title)
    if any(t in text for t in ('companion','launcher','binder')): return False
    return 'ragemultiplayer' in text or 'grandtheftauto' in text or text in ('gta5','gtav','gta5enhanced','gta5legacy')

def is_game_window(info):
    if info.get('minimized') or not info.get('visible',True): return False
    if info.get('width',0)<640 or info.get('height',0)<480: return False
    exe=info.get('exe','').casefold()
    title=info.get('title','')
    if 'companion' in normalized_title(title): return False
    if exe in GAME_PROCESSES: return True
    # Unknown process names are allowed only for an actual RAGE/GTA title. Launchers
    # and our own tools do not qualify merely because their name includes "Nova".
    return is_game_title(title)

class ProcessEntry(ctypes.Structure):
    _fields_=[('dwSize',wintypes.DWORD),('cntUsage',wintypes.DWORD),('th32ProcessID',wintypes.DWORD),('th32DefaultHeapID',ctypes.c_size_t),('th32ModuleID',wintypes.DWORD),('cntThreads',wintypes.DWORD),('th32ParentProcessID',wintypes.DWORD),('pcPriClassBase',wintypes.LONG),('dwFlags',wintypes.DWORD),('szExeFile',wintypes.WCHAR*260)]

_process_cache={}; _process_stamp=0
def process_names():
    global _process_cache,_process_stamp
    if time.monotonic()-_process_stamp<10: return _process_cache
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD]; k.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
    k.Process32FirstW.argtypes=[wintypes.HANDLE,ctypes.POINTER(ProcessEntry)]; k.Process32FirstW.restype=wintypes.BOOL
    k.Process32NextW.argtypes=[wintypes.HANDLE,ctypes.POINTER(ProcessEntry)]; k.Process32NextW.restype=wintypes.BOOL
    k.CloseHandle.argtypes=[wintypes.HANDLE]; k.CloseHandle.restype=wintypes.BOOL
    handle=k.CreateToolhelp32Snapshot(0x00000002,0)
    if handle in (None,ctypes.c_void_p(-1).value): return _process_cache
    entries={}; entry=ProcessEntry(); entry.dwSize=ctypes.sizeof(entry)
    try:
        ok=k.Process32FirstW(handle,ctypes.byref(entry))
        while ok:
            entries[entry.th32ProcessID]=entry.szExeFile.casefold()
            ok=k.Process32NextW(handle,ctypes.byref(entry))
    finally: k.CloseHandle(handle)
    _process_cache=entries; _process_stamp=time.monotonic(); return entries

def user_api():
    u=ctypes.WinDLL('user32',use_last_error=True)
    u.GetForegroundWindow.argtypes=[]; u.GetForegroundWindow.restype=wintypes.HWND
    u.GetWindowTextW.argtypes=[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int]; u.GetWindowTextW.restype=ctypes.c_int
    u.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]; u.GetWindowThreadProcessId.restype=wintypes.DWORD
    u.GetClientRect.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.RECT)]; u.GetClientRect.restype=wintypes.BOOL
    u.ClientToScreen.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.POINT)]; u.ClientToScreen.restype=wintypes.BOOL
    u.IsWindowVisible.argtypes=[wintypes.HWND]; u.IsWindowVisible.restype=wintypes.BOOL
    u.IsIconic.argtypes=[wintypes.HWND]; u.IsIconic.restype=wintypes.BOOL
    return u

def window_info(hwnd,user=None,names=None):
    u=user or user_api(); names=process_names() if names is None else names
    title=ctypes.create_unicode_buffer(1024); pid=wintypes.DWORD(); rect=wintypes.RECT(); point=wintypes.POINT(0,0)
    u.GetWindowTextW(hwnd,title,len(title)); u.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
    if not u.GetClientRect(hwnd,ctypes.byref(rect)) or not u.ClientToScreen(hwnd,ctypes.byref(point)): return None
    return {'hwnd':hwnd,'pid':pid.value,'exe':names.get(pid.value,''),'title':title.value,'visible':bool(u.IsWindowVisible(hwnd)),'minimized':bool(u.IsIconic(hwnd)),'left':point.x,'top':point.y,'width':rect.right-rect.left,'height':rect.bottom-rect.top}

def select_active_game(foreground,windows,own_pid):
    """Pure selection policy, independently tested from Windows handles/capture."""
    if not foreground: return None
    if is_game_window(foreground): return foreground
    exe=foreground.get('exe','').casefold()
    if exe in UI_PROCESSES:
        return next((w for w in windows if is_game_window(w)),None)
    # A focused compact companion panel may temporarily own input. Only allow the
    # game immediately behind our tools; a browser/main app in front pauses capture.
    if foreground.get('pid')==own_pid and 'companion' in normalized_title(foreground.get('title','')) and (foreground.get('width',0)<600 or foreground.get('height',0)<400):
        for w in windows:
            if w.get('pid')==own_pid or not w.get('visible') or w.get('minimized') or w.get('width',0)<640 or w.get('height',0)<480: continue
            return w if is_game_window(w) else None
    return None

def active_game_info():
    u=user_api(); names=process_names(); foreground=window_info(u.GetForegroundWindow(),u,names)
    if foreground and is_game_window(foreground): return foreground
    if not foreground: return None
    if foreground.get('exe') not in UI_PROCESSES and foreground.get('pid')!=os.getpid(): return None
    windows=[]; callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
    def visit(hwnd,param):
        if u.IsWindowVisible(hwnd):
            info=window_info(hwnd,u,names)
            if info: windows.append(info)
        return True
    callback=callback_type(visit); u.EnumWindows.argtypes=[callback_type,wintypes.LPARAM]; u.EnumWindows.restype=wintypes.BOOL; u.EnumWindows(callback,0)
    return select_active_game(foreground,windows,os.getpid())

def game_rect():
    info=active_game_info()
    return {k:info[k] for k in ('left','top','width','height')} if info else None
