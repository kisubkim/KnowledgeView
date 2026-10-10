"""Is Claude Code installed and logged in? (decides whether the claude_cli mode is offered)

Uses ``claude auth status`` (JSON with ``loggedIn``). The result is cached for a minute;
only non-identifying fields are kept (no email or organisation).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time

_lock = threading.Lock()
_cache: dict = {"at": 0.0, "command": None, "value": None}
MAX_AGE_S = 60


def _probe(command: str) -> dict:
    exe = shutil.which(command or "claude")
    if not exe:
        return {"installed": False, "logged_in": False, "path": "", "note": "claude 명령을 찾지 못했습니다(Claude Code 미설치)."}
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        r = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=30, cwd=tempfile.gettempdir(), creationflags=flags)
    except (OSError, subprocess.SubprocessError) as e:
        return {"installed": True, "logged_in": None, "path": exe, "note": f"로그인 상태를 확인하지 못했습니다: {e}"}
    out = r.stdout or ""
    try:
        data = json.loads(out[out.find("{"): out.rfind("}") + 1])
    except ValueError:
        # Older CLIs print text; "not logged in" style answers come with a non-zero exit code.
        logged = r.returncode == 0 and "not" not in out.lower()
        return {"installed": True, "logged_in": logged, "path": exe,
                "note": "" if logged else "Claude Code에 로그인되어 있지 않습니다. 터미널에서 claude 를 실행해 로그인하세요."}
    logged = bool(data.get("loggedIn"))
    return {
        "installed": True,
        "logged_in": logged,
        "path": exe,
        "auth_method": data.get("authMethod") or "",
        "subscription": data.get("subscriptionType") or "",
        "note": "" if logged else "Claude Code에 로그인되어 있지 않습니다. 터미널에서 claude 를 실행해 로그인하세요.",
    }


def status(command: str = "claude", force: bool = False) -> dict:
    with _lock:
        fresh = time.time() - _cache["at"] < MAX_AGE_S and _cache["command"] == command
        if fresh and not force and _cache["value"] is not None:
            return dict(_cache["value"])
    value = _probe(command)
    with _lock:
        _cache.update(at=time.time(), command=command, value=value)
    return dict(value)


def usable(st: dict) -> bool:
    """Offer the mode when installed and logged in (or when login could not be determined)."""
    return bool(st.get("installed")) and st.get("logged_in") is not False
