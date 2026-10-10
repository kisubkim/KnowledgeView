"""One running copy per user session, plus two named events other processes can signal:
SHOW (a second launch asks the running copy to open the browser) and QUIT (the
installer/uninstaller asks it to exit before replacing or deleting the exe).

Same scheme as the TokenChecker project in this workspace."""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from typing import Callable

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateMutexW.restype = wintypes.HANDLE
_k32.CreateEventW.restype = wintypes.HANDLE
_k32.OpenEventW.restype = wintypes.HANDLE
_k32.OpenMutexW.restype = wintypes.HANDLE
_k32.WaitForMultipleObjects.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE), wintypes.BOOL, wintypes.DWORD]

MUTEX = "Local\\KnowledgeView.Instance"
SHOW_EVENT = "Local\\KnowledgeView.Show"
QUIT_EVENT = "Local\\KnowledgeView.Quit"
ERROR_ALREADY_EXISTS = 183
EVENT_MODIFY_STATE = 0x0002
SYNCHRONIZE = 0x00100000
INFINITE = 0xFFFFFFFF

_mutex = None  # keep the handle alive for the process lifetime


def acquire(wait: float = 0.0) -> bool:
    """True if we are the only instance. With wait, keep trying while a previous copy exits
    (a restart launches the new copy before the old one has finished quitting)."""
    global _mutex
    deadline = time.time() + wait
    while True:
        _mutex = _k32.CreateMutexW(None, False, MUTEX)
        if ctypes.get_last_error() != ERROR_ALREADY_EXISTS:
            return True
        _k32.CloseHandle(_mutex)
        _mutex = None
        if time.time() >= deadline:
            return False
        time.sleep(0.3)


def is_running() -> bool:
    h = _k32.OpenMutexW(SYNCHRONIZE, False, MUTEX)
    if h:
        _k32.CloseHandle(h)
        return True
    return False


def signal(name: str) -> bool:
    h = _k32.OpenEventW(EVENT_MODIFY_STATE, False, name)
    if not h:
        return False
    _k32.SetEvent(h)
    _k32.CloseHandle(h)
    return True


def stop_running_instance(timeout: float = 15.0) -> bool:
    """Ask a running copy to quit and wait until it has. True if none is left running."""
    if not is_running():
        return True
    signal(QUIT_EVENT)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_running():
            time.sleep(0.8)  # let the process finish exiting and release the exe file
            return True
        time.sleep(0.2)
    return False


def listen(on_show: Callable[[], None], on_quit: Callable[[], None]) -> None:
    """Start a daemon thread that dispatches SHOW/QUIT signals (callbacks run on that thread)."""
    show = _k32.CreateEventW(None, False, False, SHOW_EVENT)
    quit_ = _k32.CreateEventW(None, False, False, QUIT_EVENT)
    handles = (wintypes.HANDLE * 2)(show, quit_)

    def run() -> None:
        while True:
            idx = _k32.WaitForMultipleObjects(2, handles, False, INFINITE)
            if idx == 0:
                on_show()
            elif idx == 1:
                on_quit()
                return
            else:
                return

    threading.Thread(target=run, name="instance-signals", daemon=True).start()
