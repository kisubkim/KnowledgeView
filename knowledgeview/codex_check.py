"""Probe Codex login without retaining CLI output, which can contain account data."""

from __future__ import annotations

import os
import json
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .config import CODEX_EFFORTS

_lock = threading.Lock()
_cache: dict = {"at": 0.0, "command": None, "value": None}
MAX_AGE_S = 60


def _probe(command: str) -> dict:
    exe = shutil.which(command or "codex")
    if not exe:
        return {"installed": False, "logged_in": False, "path": "",
                "note": "codex 명령을 찾지 못했습니다(Codex CLI 미설치)."}
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        result = subprocess.run([exe, "login", "status"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=30,
                                cwd=tempfile.gettempdir(), creationflags=flags)
    except (OSError, subprocess.SubprocessError):
        return {"installed": True, "logged_in": False, "path": exe,
                "note": "로그인 상태를 확인하지 못했습니다. 터미널에서 codex login status를 확인하세요."}
    logged = result.returncode == 0
    return {"installed": True, "logged_in": logged, "path": exe,
            "note": "" if logged else "Codex CLI에 로그인되어 있지 않습니다. 서버 PC에서 codex login을 실행하세요."}


def status(command: str = "codex", force: bool = False) -> dict:
    with _lock:
        if (not force and _cache["command"] == command and _cache["value"] is not None
                and time.time() - _cache["at"] < MAX_AGE_S):
            return dict(_cache["value"])
    value = _probe(command)
    with _lock:
        _cache.update(at=time.time(), command=command, value=value)
    return dict(value)


def usable(st: dict) -> bool:
    return bool(st.get("installed") and st.get("logged_in"))


def models() -> list[dict]:
    """Read only public model metadata from the CLI cache; never read auth files."""
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        data = json.loads((home / "models_cache.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        return []
    result = []
    for item in data["models"]:
        if not isinstance(item, dict) or item.get("visibility") == "hide" or not isinstance(item.get("slug"), str):
            continue
        levels = item.get("supported_reasoning_levels") or []
        result.append({"id": item["slug"], "label": item.get("display_name") or item["slug"],
                       "efforts": [level["effort"] for level in levels if isinstance(level, dict)
                                   and level.get("effort") in CODEX_EFFORTS]})
    return result
