"""Desktop mode: the web server runs in a thread, the tray icon owns the main thread.

The web UI's 종료 button calls ``/api/app/exit``, which reaches ``Desktop.quit`` (stop
everything) or leaves the app running in the tray. A second launch only signals SHOW,
which opens the browser on the running copy.
"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import threading
import time
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import single_instance

log = logging.getLogger("knowledgeview.desktop")
APP_NAME = "KnowledgeView"
MB_OK, MB_YESNO, MB_ICONINFO, MB_ICONWARN, IDYES = 0x0, 0x4, 0x40, 0x30, 6


def message(text: str, flags: int = MB_OK | MB_ICONINFO) -> int:
    return ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, flags)


def fresh_env() -> dict:
    """Environment for starting another copy of the exe. A onefile child inherits the parent's
    bootloader variables and would reuse the parent's temp folder, which is deleted when the
    parent exits ("No module named ..."); PyInstaller >= 6.9 resets them on this flag."""
    return dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")


def _setup_logging(data_dir: Path) -> Path:
    logs = data_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(logs / "knowledgeview.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    # A windowed (no console) build has no stdout/stderr; libraries that print must not crash.
    if sys.stdout is None or sys.stderr is None or getattr(sys, "frozen", False):
        stream = open(logs / "console.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = stream
    return logs


def _open_folder(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    os.startfile(str(path))  # noqa: S606 - opens Explorer


class Desktop:
    def __init__(self, server, url: str):
        self.server = server
        self.url = url
        self.icon = None
        self._quitting = False

    def open_browser(self) -> None:
        webbrowser.open(self.url)

    def restart(self, delay: float = 0.5) -> None:
        """Start a new copy (it waits for this one to exit, then uses the saved port) and quit.
        The new copy does not open a browser: the current tab moves to the new address itself."""
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--restarted", "--tray"] + [a for a in sys.argv[1:] if a == "--no-install"]
            cwd = str(Path(sys.executable).parent)
        else:
            cmd = [sys.executable, "-m", "knowledgeview", "--desktop", "--restarted", "--tray"]
            cwd = str(Path(__file__).resolve().parents[2])
        log.info("restarting: %s", cmd)
        subprocess.Popen(cmd, cwd=cwd, close_fds=True, env=fresh_env(),
                         creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        self.quit(delay)

    def quit(self, delay: float = 0.0) -> None:
        if self._quitting:
            return
        self._quitting = True
        threading.Timer(delay, self._stop).start()

    def _stop(self) -> None:
        log.info("quitting")
        self.server.should_exit = True
        if self.icon is not None:
            self.icon.stop()


def run(open_browser: bool = True, restarted: bool = False) -> int:
    from ..config import data_dir, server_address

    logs = _setup_logging(data_dir())
    if not single_instance.acquire(wait=30.0 if restarted else 0.0):
        single_instance.signal(single_instance.SHOW_EVENT)
        return 0

    import pystray
    import uvicorn

    from .. import main
    from .icon import render_icon

    host, port = server_address()
    main.app.state.port = port
    url = f"http://127.0.0.1:{port}/" if host in ("0.0.0.0", "::") else f"http://{host}:{port}/"
    server = uvicorn.Server(uvicorn.Config(main.app, host=host, port=port, log_config=None, access_log=False))
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()
    deadline = time.time() + 30
    while not server.started and thread.is_alive() and time.time() < deadline:
        time.sleep(0.1)
    if not server.started:
        log.error("server did not start on %s:%s", host, port)
        message(f"서버를 시작하지 못했습니다({host}:{port}).\n다른 프로그램이 포트 {port}를 쓰고 있을 수 있습니다.\n"
                f"로그: {logs}", MB_OK | MB_ICONWARN)
        return 1
    log.info("serving on %s:%s", host, port)

    desktop = Desktop(server, url)
    main.app.state.desktop = desktop

    def active_jobs() -> int:
        return sum(1 for j in main.jobs.list() if j["status"] in ("queued", "running"))

    def on_quit(icon=None, item=None) -> None:
        n = active_jobs()
        if n and message(f"진행 중이거나 대기 중인 작업이 {n}개 있습니다. 취소하고 종료할까요?", MB_YESNO | MB_ICONWARN) != IDYES:
            return
        for j in main.jobs.list():
            if j["status"] in ("queued", "running"):
                main.jobs.cancel(j["id"])
        desktop.quit()

    menu = pystray.Menu(
        pystray.MenuItem("열기", lambda icon, item: desktop.open_browser(), default=True),
        pystray.MenuItem("출력 폴더 열기", lambda icon, item: _open_folder(main.settings.output_dir())),
        pystray.MenuItem("데이터 폴더 열기", lambda icon, item: _open_folder(data_dir())),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("종료", on_quit),
    )
    desktop.icon = pystray.Icon(APP_NAME, render_icon(64), f"{APP_NAME} — {url}", menu)
    single_instance.listen(on_show=desktop.open_browser, on_quit=lambda: desktop.quit())
    if open_browser:
        threading.Timer(0.3, desktop.open_browser).start()
    desktop.icon.run()  # blocks until desktop.quit()

    server.should_exit = True
    thread.join(timeout=10)
    log.info("stopped")
    return 0
