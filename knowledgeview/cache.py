"""Small JSON file cache for LLM results and figures (``<data>/cache/<kind>/<sha>.json``).

Keys hash everything that changes the answer (prompt text, mode, model, input),
so regenerating a page after a template change does not call the LLM again.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def key(*parts: object) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(json.dumps(p, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


class Cache:
    def __init__(self, folder: Path | None, enabled: bool = True):
        self.folder = folder
        self.read_enabled = enabled and folder is not None

    def _path(self, kind: str, k: str) -> Path:
        return self.folder / kind / k[:2] / f"{k}.json"  # type: ignore[operator]

    def get(self, kind: str, k: str):
        if not self.read_enabled:
            return None
        p = self._path(kind, k)
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def put(self, kind: str, k: str, value) -> None:
        """Always written (even when reads are disabled) so a fresh run refreshes the cache."""
        if self.folder is None:
            return
        p = self._path(kind, k)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, p)
        except OSError:
            pass
