import json
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from knowledgeview import codex_check
from knowledgeview.config import Settings, mode_ids, mode_label
from knowledgeview.llm import Cancelled, CodexCliClient, LLMError, make_client


@pytest.mark.parametrize("returncode,logged", [(0, True), (1, False)])
def test_login_probe_discards_account_output(monkeypatch, returncode, logged):
    monkeypatch.setattr(codex_check.shutil, "which", lambda _: "codex.exe")

    def run(args, **kwargs):
        assert args == ["codex.exe", "login", "status"]
        return SimpleNamespace(returncode=returncode, stdout="private account data", stderr="private credential")

    monkeypatch.setattr(codex_check.subprocess, "run", run)
    result = codex_check._probe("codex")
    assert result["logged_in"] is logged
    assert codex_check.usable(result) is logged
    assert "private" not in json.dumps(result)


def test_login_missing_timeout_and_cache(monkeypatch):
    monkeypatch.setattr(codex_check.shutil, "which", lambda _: None)
    assert not codex_check._probe("missing")["installed"]
    monkeypatch.setattr(codex_check.shutil, "which", lambda _: "codex")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("codex", 30)

    monkeypatch.setattr(codex_check.subprocess, "run", timeout)
    assert not codex_check.usable(codex_check._probe("codex"))
    calls = []
    monkeypatch.setattr(codex_check, "_cache", {"at": 0, "command": None, "value": None})

    def probe(command):
        calls.append(command)
        return {"installed": True, "logged_in": True}

    monkeypatch.setattr(codex_check, "_probe", probe)
    codex_check.status("a")
    codex_check.status("a")
    codex_check.status("a", force=True)
    codex_check.status("b")
    assert calls == ["a", "a", "b"]


@pytest.mark.parametrize("behavior", ["success", "failed", "empty", "missing", "timeout", "cancel"])
def test_cli_final_response_errors_and_cleanup(monkeypatch, behavior):
    calls = []
    cancel = threading.Event()

    class Process:
        returncode = None
        killed = False

        def __init__(self, args, **kwargs):
            self.args = args
            self.workdir = kwargs["cwd"]
            calls.append(self)
            assert not list(Path(self.workdir).iterdir())

        def poll(self):
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = -1

        def communicate(self, text=None, timeout=None):
            if text is None:
                return "", ""
            assert "system instruction" in text and "한글 입력" in text
            if behavior == "timeout":
                raise subprocess.TimeoutExpired(self.args, timeout)
            if behavior == "cancel":
                cancel.set()
                assert cancelled.wait(2)
            self.returncode = 1 if behavior == "failed" else 0
            if behavior != "missing":
                output = Path(self.args[self.args.index("--output-last-message") + 1])
                output.write_text("" if behavior == "empty" else "# 최종 문서", encoding="utf-8")
            return "progress text, not the document", "CLI failure"

    cancelled = threading.Event()
    original_kill = Process.kill

    def kill(proc):
        original_kill(proc)
        cancelled.set()

    monkeypatch.setattr(Process, "kill", kill)
    monkeypatch.setattr("knowledgeview.llm.subprocess.Popen", Process)
    client = CodexCliClient({"command": "custom-codex", "model": "test-model", "reasoning_effort": "high", "timeout_s": 2}, cancel)
    if behavior == "success":
        assert client.complete("system instruction", "한글 입력") == "# 최종 문서"
    else:
        with pytest.raises(Cancelled if behavior == "cancel" else LLMError):
            client.complete("system instruction", "한글 입력")
    proc = calls[0]
    assert not Path(proc.workdir).exists()
    assert "--ignore-user-config" in proc.args and "--ephemeral" in proc.args
    assert proc.args[proc.args.index("--sandbox") + 1] == "read-only"
    assert "features.shell_tool=false" in proc.args
    assert 'model_reasoning_effort="high"' in proc.args
    assert proc.args[proc.args.index("--model") + 1] == "test-model"
    if behavior in ("timeout", "cancel"):
        assert proc.killed


def test_cli_pre_cancel_and_missing_executable(monkeypatch):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Cancelled):
        CodexCliClient({}, cancel).complete("", "")

    def missing(*args, **kwargs):
        raise FileNotFoundError("missing codex")

    monkeypatch.setattr("knowledgeview.llm.subprocess.Popen", missing)
    with pytest.raises(LLMError, match="실행 파일"):
        CodexCliClient({}).complete("", "")


def test_settings_migration_and_codex_mode(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"claude": {"model": "opus"}}', encoding="utf-8")
    settings = Settings(path)
    assert settings.get()["codex"]["command"] == "codex"
    settings.update({"default_mode": "codex_cli", "codex": {"model": "test-model", "concurrency": 2}})
    loaded = Settings(path).get()
    assert loaded["claude"]["model"] == "opus"
    assert loaded["default_mode"] in mode_ids(loaded)
    assert mode_label("codex_cli") == "Codex CLI 상세화"
    assert isinstance(make_client("codex_cli", loaded), CodexCliClient)
    assert make_client("codex_cli", loaded).model == "test-model"
    settings.update({"codex": {"reasoning_effort": "high"}})
    assert Settings(path).get()["codex"]["reasoning_effort"] == "high"
    with pytest.raises(ValueError):
        settings.update({"codex": {"reasoning_effort": "invalid"}})


def test_models_cache_returns_only_public_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert codex_check.models() == []
    cache = tmp_path / "models_cache.json"
    cache.write_text(json.dumps({"models": [
        {"slug": "test-model", "display_name": "Test", "secret": "private",
         "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}]},
        {"slug": "hidden", "visibility": "hide"},
    ]}), encoding="utf-8")
    assert codex_check.models() == [{"id": "test-model", "label": "Test", "efforts": ["low", "high"]}]
    cache.write_text("invalid", encoding="utf-8")
    assert codex_check.models() == []


def test_api_mode_visibility_validation_and_admin(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from knowledgeview import main

    monkeypatch.setattr(main, "settings", Settings(tmp_path / "settings.json"))
    monkeypatch.setattr(main, "admin_token", lambda: "test-token")
    monkeypatch.setattr(main, "_claude_status", lambda: {"installed": False, "logged_in": False})
    state = {"installed": True, "logged_in": True, "path": "codex", "note": ""}
    monkeypatch.setattr(main, "_codex_status", lambda force=False: dict(state))
    with TestClient(main.app) as client:
        data = client.get("/api/status").json()
        assert "codex_cli" in [m["id"] for m in data["modes"]]
        assert "path" not in data["codex"]
        assert main._check_mode("codex_cli") == "codex_cli"
        assert client.post("/api/codex/check").status_code == 401
        response = client.post("/api/codex/check", headers={"X-KV-Token": "test-token"})
        assert response.status_code == 200 and response.json()["logged_in"]
        state["logged_in"] = False
        assert "codex_cli" not in [m["id"] for m in client.get("/api/status").json()["modes"]]
        with pytest.raises(main.HTTPException) as error:
            main._check_mode("codex_cli")
        assert error.value.status_code == 400


def test_codex_generation_preserves_source_and_metadata(tmp_path):
    from knowledgeview.generate import generate
    from knowledgeview.library import list_pages
    from knowledgeview.llm import LLMClient

    source = tmp_path / "source"
    source.mkdir()
    markdown = "# 테스트\n\n## 개요\n\n원문입니다.\n\n```mermaid\ngraph LR\n A-->B\n```\n"
    path = source / "sample.md"
    path.write_text(markdown, encoding="utf-8")

    class FakeCodex(LLMClient):
        model = "test-codex"

        def complete(self, system, user):
            if "<<<DOC" in user:
                return '{"summary":"요약","glossary":[],"quiz":[]}'
            section = user.split("<<<SECTION\n", 1)[1].rsplit("\nSECTION>>>", 1)[0]
            return section + "\n\n> [!DETAIL] 보강\n> Codex 해설\n"

    messages = []
    output = tmp_path / "output"
    generate(source_path=path, source_root=source, category="", mode="codex_cli",
             settings={"codex": {"concurrency": 2}}, output_dir=output,
             client=FakeCodex(), progress=lambda done, total, message: messages.append(message))
    html = (output / "sample.html").read_text(encoding="utf-8")
    assert "Codex 해설" in html and '<pre class="mermaid">' in html
    page = list_pages(output)[0]
    assert page["mode"] == "codex_cli" and page["model"] == "test-codex"
    assert path.read_text(encoding="utf-8") == markdown
    assert any("동시 2개" in message for message in messages)
