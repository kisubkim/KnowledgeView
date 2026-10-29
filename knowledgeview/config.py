"""Paths and persisted settings.

Process-level options come from environment variables (KV_DATA_DIR, KV_ADMIN_TOKEN) or,
for an installed copy, from ``knowledgeview.json`` next to the exe (written by the installer:
data_dir, host, port, admin_token). Everything a user can change in the web UI lives in
``<data>/settings.json``.
"""

from __future__ import annotations

import copy
import json
import os
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

PACKAGE_DIR = Path(__file__).resolve().parent  # inside the PyInstaller bundle when frozen
FROZEN = bool(getattr(sys, "frozen", False))
# Base for relative paths and the install config: the exe's folder when frozen, else the repo.
APP_ROOT = Path(sys.executable).resolve().parent if FROZEN else PACKAGE_DIR.parent
INSTALL_CONFIG_NAME = "knowledgeview.json"
DEFAULT_PORT = 8770

BASE_MODES = ("none", "claude_cli", "codex_cli")
CODEX_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
OPENAI_PREFIX = "openai:"  # mode id of an OpenAI-compatible endpoint: "openai:<endpoint id>"

DEFAULTS: dict[str, Any] = {
    "source_roots": [] if FROZEN else [r"D:\AI_Docs\docs_base"],
    "output_dir": "output",
    "default_mode": "none",
    # Sections whose heading matches one of these (substring) are never sent to an LLM.
    "skip_headings": ["참고 문헌", "참고문헌", "References"],
    # Figures in LLM modes: Wikimedia Commons first, then an LLM-drawn SVG.
    "images": {
        "enabled": True,
        "web_search": True,
        "svg_fallback": True,
        "max_per_section": 2,
        "max_bytes": 1_500_000,
    },
    "claude": {
        "command": "claude",
        "model": "",
        "timeout_s": 300,
        "concurrency": 3,
    },
    "codex": {
        "command": "codex",
        "model": "",
        "reasoning_effort": "",
        "timeout_s": 300,
        "concurrency": 3,
    },
    # OpenAI-compatible endpoints; each one is its own generation mode ("openai:<id>").
    "openai_endpoints": [],
    # Overwritten pages are moved to <output>/.history/; keep this many per page (0 = all).
    "history_keep": 20,
    # Desktop app: what the web UI's 종료 button does ("ask", "quit" or "tray").
    "exit_action": "ask",
}

ENDPOINT_DEFAULTS: dict[str, Any] = {
    "id": "",
    "name": "",
    "base_url": "",
    "api_key": "",
    "model": "",
    "temperature": 0.3,
    "max_tokens": 8192,
    "timeout_s": 900,
    "concurrency": 2,
    # Sent as "reasoning_effort" when set ("none" turns thinking off on Ollama, vLLM chat templates vary).
    "reasoning_effort": "",
}


def _normalize_endpoints(items: list, previous: list) -> list[dict]:
    """Fill defaults and ids; an empty api_key keeps the stored key unless clear_api_key is set."""
    old = {e.get("id"): e for e in previous if isinstance(e, dict)}
    out, seen = [], set()
    for raw in items:
        if not isinstance(raw, dict):
            continue
        raw = dict(raw)
        clear = raw.pop("clear_api_key", False)
        raw.pop("has_api_key", None)
        ep = {k: raw.get(k, v) for k, v in ENDPOINT_DEFAULTS.items()}
        ep["id"] = str(ep["id"] or "").strip() or uuid.uuid4().hex[:8]
        while ep["id"] in seen:
            ep["id"] = uuid.uuid4().hex[:8]
        seen.add(ep["id"])
        prev = old.get(ep["id"], {})
        if clear:
            ep["api_key"] = ""
        elif not ep["api_key"]:
            ep["api_key"] = prev.get("api_key", "")
        ep["name"] = str(ep["name"]).strip() or ep["model"] or ep["base_url"] or ep["id"]
        ep["base_url"] = str(ep["base_url"]).strip().rstrip("/")
        out.append(ep)
    return out


def mode_ids(data: dict) -> list[str]:
    return list(BASE_MODES) + [OPENAI_PREFIX + e["id"] for e in data.get("openai_endpoints", [])]


def endpoint_for(mode: str, data: dict) -> dict | None:
    eps = data.get("openai_endpoints", [])
    if mode == "openai":  # pages made before endpoints became a list
        return eps[0] if eps else None
    if mode.startswith(OPENAI_PREFIX):
        return next((e for e in eps if e["id"] == mode[len(OPENAI_PREFIX):]), None)
    return None


def mode_label(mode: str, data: dict | None = None) -> str:
    if mode == "none":
        return "구조화 (LLM 없음)"
    if mode == "claude_cli":
        return "Claude Code CLI 상세화"
    if mode == "codex_cli":
        return "Codex CLI 상세화"
    ep = endpoint_for(mode, data or {})
    return f"OpenAI 호환 상세화 ({ep['name']})" if ep else "OpenAI 호환 상세화"


def is_openai(mode: str) -> bool:
    return mode == "openai" or mode.startswith(OPENAI_PREFIX)


def _resolve(p: str | Path) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (APP_ROOT / path)


def install_config() -> dict:
    """Settings fixed at install time (``knowledgeview.json`` next to the exe), or {}."""
    try:
        data = json.loads((APP_ROOT / INSTALL_CONFIG_NAME).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def install_config_path() -> Path:
    return APP_ROOT / INSTALL_CONFIG_NAME


def save_install_config(updates: dict) -> Path:
    """Merge updates into knowledgeview.json (created when missing, e.g. a source checkout)."""
    path = install_config_path()
    data = install_config()
    data.update(updates)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def port_overridden() -> bool:
    """KV_PORT in the environment wins over knowledgeview.json."""
    return bool(os.environ.get("KV_PORT"))


def data_dir() -> Path:
    d = _resolve(os.environ.get("KV_DATA_DIR") or install_config().get("data_dir") or "data")
    d.mkdir(parents=True, exist_ok=True)
    return d


def admin_token() -> str:
    return (os.environ.get("KV_ADMIN_TOKEN") or install_config().get("admin_token") or "").strip()


def server_address() -> tuple[str, int]:
    cfg = install_config()
    host = os.environ.get("KV_HOST") or cfg.get("host") or "127.0.0.1"
    port = int(os.environ.get("KV_PORT") or cfg.get("port") or DEFAULT_PORT)
    return host, port


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if k not in base:
            continue
        if isinstance(base[k], dict) and isinstance(v, dict):
            out[k] = _merge(base[k], v)
        else:
            out[k] = v
    return out


class Settings:
    """Thread-safe JSON settings store."""

    def __init__(self, path: Path | None = None):
        self.path = path or (data_dir() / "settings.json")
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = {}
            # Migrate the single "openai" block of version 0.1 into the endpoint list.
            legacy = raw.pop("openai", None)
            if isinstance(legacy, dict) and legacy.get("base_url") and not raw.get("openai_endpoints"):
                raw["openai_endpoints"] = [{**legacy, "id": "default", "name": legacy.get("model") or "기본"}]
            if raw.get("default_mode") == "openai":
                raw["default_mode"] = OPENAI_PREFIX + "default" if raw.get("openai_endpoints") else "none"
            data = _merge(DEFAULTS, raw)
            data["openai_endpoints"] = _normalize_endpoints(data["openai_endpoints"], data["openai_endpoints"])
            return data
        return copy.deepcopy(DEFAULTS)

    def get(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._data)

    def public(self) -> dict:
        """Settings safe to send to the browser (no API key)."""
        d = self.get()
        for ep in d["openai_endpoints"]:
            ep["has_api_key"] = bool(ep.pop("api_key"))
        d["output_dir_resolved"] = str(self.output_dir())
        return d

    def update(self, patch: dict) -> dict:
        with self._lock:
            patch = copy.deepcopy(patch)
            new = _merge(self._data, patch)
            if not isinstance(new["codex"], dict):
                raise ValueError("Codex 설정 형식이 잘못되었습니다.")
            codex = new["codex"]
            if codex.get("reasoning_effort", "") not in ("", *CODEX_EFFORTS):
                raise ValueError("지원하지 않는 Codex 추론 강도입니다.")
            if not 1 <= int(codex.get("concurrency", 3)) <= 8:
                raise ValueError("Codex 동시 호출 수는 1~8 사이여야 합니다.")
            if float(codex.get("timeout_s", 300)) <= 0:
                raise ValueError("Codex 제한 시간은 0보다 커야 합니다.")
            if isinstance(patch.get("openai_endpoints"), list):
                new["openai_endpoints"] = _normalize_endpoints(patch["openai_endpoints"], self._data["openai_endpoints"])
            if new["default_mode"] not in mode_ids(new):
                if not is_openai(new["default_mode"]):
                    raise ValueError(f"알 수 없는 모드: {new['default_mode']}")
                new["default_mode"] = "none"  # its endpoint was deleted
            new["history_keep"] = max(0, int(new.get("history_keep") or 0))
            if new.get("exit_action") not in ("ask", "quit", "tray"):
                new["exit_action"] = "ask"
            new["source_roots"] = [str(r).strip() for r in new["source_roots"] if str(r).strip()]
            self._data = new
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
        return self.get()

    def output_dir(self) -> Path:
        d = _resolve(self.get()["output_dir"])
        d.mkdir(parents=True, exist_ok=True)
        return d

    def uploads_dir(self) -> Path:
        d = data_dir() / "uploads"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def source_roots(self) -> list[Path]:
        """Configured roots plus the uploads folder (always last)."""
        roots = [Path(r) for r in self.get()["source_roots"]]
        return roots + [self.uploads_dir()]
