from pathlib import Path, PurePosixPath

import pytest

from knowledgeview import library
from knowledgeview.cache import Cache
from knowledgeview.generate import generate, make_link_resolver
from knowledgeview.llm import LLMClient, LLMError


class FakeClient(LLMClient):
    model = "test-model"

    def __init__(self, effort="", fail=False):
        super().__init__()
        self.reasoning_effort = effort
        self.calls = 0
        self.fail = fail

    def complete(self, system, user):
        self.calls += 1
        if self.fail:
            raise LLMError("test failure")
        if "<<<DOC" in user:
            return '{"summary":"요약"}'
        section = user.split("<<<SECTION\n", 1)[1].rsplit("\nSECTION>>>", 1)[0]
        return section + "\n\n> [!DETAIL] 보강\n> 추가 설명\n"


def source_file(tmp_path, name="sample.md"):
    path = tmp_path / "source" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# 문서\n\n## 개요\n\n원문을 보존합니다.\n", encoding="utf-8")
    return path


def build(tmp_path, source, mode, **kwargs):
    client = kwargs.pop("client", FakeClient())
    return generate(source_path=source, source_root=source.parent, category="", mode=mode,
                    settings={}, output_dir=tmp_path / "output", client=client, **kwargs)


def test_provider_versions_coexist_and_archive_independently(tmp_path):
    source = source_file(tmp_path)
    original = source.read_bytes()
    out = tmp_path / "output"
    claude = build(tmp_path, source, "claude_cli")
    old_claude = (out / claude["rel"]).read_bytes()
    codex = build(tmp_path, source, "codex_cli")
    assert claude["rel"] == "sample.html"
    assert codex["rel"] == "sample--codex.html"
    assert (out / claude["rel"]).read_bytes() == old_claude
    assert claude["archived"] is codex["archived"] is None
    again = build(tmp_path, source, "codex_cli")
    assert again["rel"] == codex["rel"] and again["archived"]
    assert library.history_count(out, claude["rel"]) == 0
    assert library.history_count(out, codex["rel"]) == 1
    pages = library.list_pages(out)
    assert len(pages) == 2 and all(len(p["versions"]) == 2 for p in pages)
    assert {v["mode"] for v in pages[0]["versions"]} == {"claude_cli", "codex_cli"}
    assert source.read_bytes() == original


def test_legacy_moved_filenames_and_unrelated_source_collisions(tmp_path):
    first = source_file(tmp_path, "a/sample.md")
    second = source_file(tmp_path, "b/sample.md")
    out = tmp_path / "output"
    one = build(tmp_path, first, "codex_cli")
    two = build(tmp_path, second, "codex_cli")
    assert one["rel"] != two["rel"]
    assert build(tmp_path, second, "codex_cli")["rel"] == two["rel"]
    custom = "renamed.html"
    (out / one["rel"]).rename(out / custom)
    assert build(tmp_path, first, "codex_cli", output_rel=custom)["rel"] == custom
    assert library.history_count(out, custom) == 1
    assert len(library.list_pages(out)) == 2
    assert all(len(page["versions"]) == 1 for page in library.list_pages(out))


def test_wrong_output_target_cannot_replace_other_provider(tmp_path):
    source = source_file(tmp_path)
    old = build(tmp_path, source, "claude_cli")
    before = (tmp_path / "output" / old["rel"]).read_bytes()
    with pytest.raises(library.PathError):
        build(tmp_path, source, "codex_cli", output_rel=old["rel"])
    assert (tmp_path / "output" / old["rel"]).read_bytes() == before
    with pytest.raises(library.PathError):
        build(tmp_path, source, "codex_cli", output_rel="../escape.html")
    with pytest.raises(library.PathError):
        build(tmp_path, source, "codex_cli", output_rel="other/sample.html")


def test_failed_other_version_keeps_existing_page(tmp_path):
    source = source_file(tmp_path)
    old = build(tmp_path, source, "claude_cli")
    before = (tmp_path / "output" / old["rel"]).read_bytes()
    with pytest.raises(LLMError):
        build(tmp_path, source, "codex_cli", client=FakeClient(fail=True))
    assert (tmp_path / "output" / old["rel"]).read_bytes() == before
    assert len(library.list_pages(tmp_path / "output")) == 1


def test_links_prefer_same_provider_variant(tmp_path):
    source = source_file(tmp_path)
    target = source_file(tmp_path, "next.md")
    build(tmp_path, target, "claude_cli")
    codex = build(tmp_path, target, "codex_cli")
    resolver = make_link_resolver(source, source.parent, PurePosixPath("sample--codex.html"),
                                  output_dir=tmp_path / "output", mode="codex_cli")
    assert resolver("next.md#section") == codex["rel"] + "#section"


def test_effort_changes_cache_and_page_metadata(tmp_path):
    source = source_file(tmp_path)
    cache = Cache(tmp_path / "cache")
    low = FakeClient("low")
    result = build(tmp_path, source, "codex_cli", client=low, cache=cache)
    assert low.calls == 2
    cached = FakeClient("low")
    build(tmp_path, source, "codex_cli", client=cached, cache=cache)
    assert cached.calls == 0
    high = FakeClient("high")
    build(tmp_path, source, "codex_cli", client=high, cache=cache)
    assert high.calls == 2
    meta = library.read_meta(tmp_path / "output" / result["rel"])
    assert meta["reasoning_effort"] == "high"


def test_regeneration_api_keeps_same_mode_path_and_separates_other_mode(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from knowledgeview import main
    from knowledgeview.config import Settings

    source = source_file(tmp_path)
    old = build(tmp_path, source, "claude_cli")
    settings = Settings(tmp_path / "settings.json")
    settings.update({"source_roots": [str(source.parent)], "output_dir": str(tmp_path / "output")})
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "admin_token", lambda: "")
    monkeypatch.setattr(main, "_check_mode", lambda mode: mode)
    submitted = []

    def submit(*args, **kwargs):
        submitted.append((args, kwargs))
        return {"mode": args[4]}

    monkeypatch.setattr(main, "_submit", submit)
    with TestClient(main.app) as client:
        same = client.post("/api/pages/regenerate", json={"rels": [old["rel"]], "mode": "claude_cli"})
        assert same.status_code == 200 and submitted[-1][1]["output_rel"] == old["rel"]
        other = client.post("/api/pages/regenerate", json={"rels": [old["rel"]], "mode": "codex_cli"})
        assert other.status_code == 200 and submitted[-1][1]["output_rel"] is None
        sources = client.get("/api/sources").json()
        assert sources["roots"][0]["files"][0]["page_modes"] == ["claude_cli"]
