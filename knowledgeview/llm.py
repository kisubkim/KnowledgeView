"""LLM backends: the local Claude Code CLI (``claude -p``) or an OpenAI-compatible endpoint."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading

import httpx

from .config import endpoint_for, is_openai


class LLMError(RuntimeError):
    pass


class Cancelled(RuntimeError):
    pass


class LLMClient:
    label = ""
    model = ""

    def __init__(self, cancel: threading.Event | None = None):
        self.cancel = cancel or threading.Event()

    def complete(self, system: str, user: str) -> str:  # pragma: no cover - interface
        raise NotImplementedError


class ClaudeCliClient(LLMClient):
    """Runs ``claude -p`` with tools disabled. Uses the CLI's own login (subscription or key)."""

    label = "Claude Code CLI"

    def __init__(self, cfg: dict, cancel: threading.Event | None = None):
        super().__init__(cancel)
        cmd = cfg.get("command") or "claude"
        self.exe = shutil.which(cmd) or cmd
        self.model = cfg.get("model") or ""
        self.timeout = float(cfg.get("timeout_s") or 900)
        # An empty working dir keeps project CLAUDE.md files out of the prompt.
        self.workdir = tempfile.mkdtemp(prefix="kv-claude-")
        self._procs: set[subprocess.Popen] = set()
        self._lock = threading.Lock()

    def complete(self, system: str, user: str) -> str:
        if self.cancel.is_set():
            raise Cancelled()
        args = [self.exe, "-p", "--tools", "", "--no-session-persistence", "--output-format", "text",
                "--system-prompt", system]
        if self.model:
            args += ["--model", self.model]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    cwd=self.workdir, encoding="utf-8", errors="replace", creationflags=flags)
        except OSError as e:
            raise LLMError(f"claude 실행 파일을 찾지 못했습니다 ({self.exe}): {e}") from e
        with self._lock:
            self._procs.add(proc)
        watcher = threading.Thread(target=self._watch_cancel, args=(proc,), daemon=True)
        watcher.start()
        try:
            out, err = proc.communicate(user, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            raise LLMError(f"claude 응답 시간 초과 ({self.timeout:.0f}초)")
        finally:
            with self._lock:
                self._procs.discard(proc)
        if self.cancel.is_set():
            raise Cancelled()
        if proc.returncode != 0:
            msg = (err or out or "").strip()[-800:]
            raise LLMError(f"claude 종료 코드 {proc.returncode}: {msg}")
        return out

    def _watch_cancel(self, proc: subprocess.Popen) -> None:
        while proc.poll() is None:
            if self.cancel.wait(0.5):
                proc.kill()
                return


class OpenAIClient(LLMClient):
    label = "OpenAI 호환"

    def __init__(self, cfg: dict, cancel: threading.Event | None = None):
        super().__init__(cancel)
        self.base_url = (cfg.get("base_url") or "").rstrip("/")
        if not self.base_url:
            raise LLMError("OpenAI 호환 엔드포인트 base_url이 설정되지 않았습니다.")
        self.model = cfg.get("model") or ""
        if not self.model:
            raise LLMError("OpenAI 호환 엔드포인트의 모델 이름이 설정되지 않았습니다.")
        self.api_key = cfg.get("api_key") or ""
        self.temperature = float(cfg.get("temperature", 0.3))
        self.max_tokens = int(cfg.get("max_tokens") or 8192)
        self.timeout = float(cfg.get("timeout_s") or 900)
        self.reasoning_effort = (cfg.get("reasoning_effort") or "").strip()

    def complete(self, system: str, user: str) -> str:
        if self.cancel.is_set():
            raise Cancelled()
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        try:
            r = httpx.post(f"{self.base_url}/chat/completions", json=body, headers=headers, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise LLMError(f"엔드포인트 호출 실패: {e}") from e
        if self.cancel.is_set():
            raise Cancelled()
        if r.status_code != 200:
            raise LLMError(f"엔드포인트 응답 {r.status_code}: {r.text[:500]}")
        try:
            choice = r.json()["choices"][0]
            text = choice["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise LLMError(f"응답 형식을 해석하지 못했습니다: {r.text[:300]}") from e
        if choice.get("finish_reason") == "length":
            msg = choice.get("message") or {}
            if len(msg.get("reasoning") or msg.get("reasoning_content") or "") > len(text):
                raise LLMError("응답이 max_tokens에서 잘렸습니다. 모델이 추론(reasoning)에 토큰을 대부분 썼습니다. "
                               "엔드포인트 설정에서 추론 강도를 none으로 하거나 max_tokens를 늘리세요.")
            raise LLMError("응답이 max_tokens에서 잘렸습니다. 설정에서 max_tokens를 늘리세요.")
        return text


def make_client(mode: str, settings: dict, cancel: threading.Event | None = None) -> LLMClient:
    if mode == "claude_cli":
        return ClaudeCliClient(settings["claude"], cancel)
    if is_openai(mode):
        ep = endpoint_for(mode, settings)
        if ep is None:
            raise LLMError(f"OpenAI 호환 엔드포인트를 찾지 못했습니다(삭제되었을 수 있음): {mode}")
        return OpenAIClient(ep, cancel)
    raise LLMError(f"LLM을 쓰지 않는 모드입니다: {mode}")
