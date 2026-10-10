import threading
from pathlib import Path

import pytest

from knowledgeview import library, mdparse
from knowledgeview.generate import generate, validate_section
from knowledgeview.library import PathError
from knowledgeview.llm import LLMClient

SAMPLE = """# 샘플 문서

> **문서 버전**: 2026-10-04
> **선행 지식**: 행렬

---

## 1. 개요

본문입니다. [다음](../b/next.md#x) 문서.

```mermaid
graph LR
    A --> B
```

```bash
# 이건 제목이 아니다
echo hi
```

> **핵심 포인트**: 가중치를 찾는 과정이다.

## 2. 세부

### 2.1 하위

| a | b |
|---|---|
| 1 | 2 |

## 참고 문헌

- 논문
"""


def test_parse_splits_sections_outside_fences():
    doc = mdparse.parse(SAMPLE, "x")
    assert doc.title == "샘플 문서"
    assert doc.meta == [("문서 버전", "2026-10-04"), ("선행 지식", "행렬")]
    assert [s.heading for s in doc.sections] == ["1. 개요", "2. 세부", "참고 문헌"]
    assert "# 이건 제목이 아니다" in doc.sections[0].markdown
    assert doc.sections[0].mermaid_count == 1


def test_clean_rel_rejects_traversal():
    for bad in ["../x", "a/../../b", "a/b:c", "C:/x", "a\\..\\b"]:
        with pytest.raises(PathError):
            library.clean_rel(bad)
    assert library.clean_rel(" a / b ").as_posix() == "a/b"


def test_validate_section():
    orig = "## 1. 개요\n\n본문\n\n```mermaid\ngraph LR\nA-->B\n```"
    assert validate_section(orig, orig + "\n\n> [!DETAIL] 해설\n> 추가") is None
    assert validate_section(orig, "## 1. 개요\n\n본문 " * 10) == "mermaid 다이어그램이 빠짐"
    assert "제목" in validate_section(orig, "## 다른 제목\n" + orig[8:] + "x" * 50)


def _write_source(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "src"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir()
    (root / "b" / "next.md").write_text("# 다음\n", encoding="utf-8")
    p = root / "a" / "sample.md"
    p.write_text(SAMPLE, encoding="utf-8")
    return root, p


SETTINGS = {"skip_headings": ["참고 문헌"], "claude": {"concurrency": 2}, "openai": {"concurrency": 2}}


def test_generate_none_mode(tmp_path):
    root, p = _write_source(tmp_path)
    out = tmp_path / "out"
    res = generate(source_path=p, source_root=root, category="a", mode="none", settings=SETTINGS, output_dir=out)
    assert res["rel"] == "a/sample.html"
    html = (out / "a" / "sample.html").read_text(encoding="utf-8")
    assert '<pre class="mermaid">' in html
    assert 'href="../b/next.html#x"' in html
    assert "kv-c-key" in html
    pages = library.list_pages(out)
    assert pages[0]["title"] == "샘플 문서" and pages[0]["stale"] is False
    p.write_text(SAMPLE + "\n추가", encoding="utf-8")
    assert library.list_pages(out)[0]["stale"] is True


class FakeLLM(LLMClient):
    model = "fake"

    def __init__(self, break_heading: str | None = None):
        super().__init__(threading.Event())
        self.break_heading = break_heading
        self.sections_seen: list[str] = []

    def complete(self, system, user):
        if "<<<DOC" in user:
            return '설명 {"summary": "요약", "glossary": [{"term": "BPE", "desc": "설명"}], "quiz": [{"q": "Q?", "a": "A"}]}'
        sec = user.split("<<<SECTION\n", 1)[1].rsplit("\nSECTION>>>", 1)[0]
        self.sections_seen.append(sec.splitlines()[0])
        if self.break_heading and self.break_heading in sec:
            return "엉뚱한 답"
        return "```markdown\n" + sec + "\n\n> [!DETAIL] 더 보기\n> 보강된 해설\n```"


def test_generate_llm_mode_with_fallback(tmp_path):
    root, p = _write_source(tmp_path)
    out = tmp_path / "out"
    llm = FakeLLM(break_heading="## 2. 세부")
    res = generate(source_path=p, source_root=root, category="", mode="openai", settings=SETTINGS,
                   output_dir=out, client=llm)
    assert "## 참고 문헌" not in llm.sections_seen
    html = (out / "sample.html").read_text(encoding="utf-8")
    assert "kv-c-detail kv-added" in html
    assert "보강 실패로 원문을 그대로 표시합니다" in html
    assert "용어 사전" in html and "BPE" in html
    assert any("2. 세부" in w for w in res["warnings"])


def test_normalize_section():
    from knowledgeview.generate import normalize_section
    orig = "## 1. 개요\n\n본문"
    assert normalize_section(orig, "다음은 보강한 섹션입니다.\n\n## 1. 개요\n\n본문") == "## 1. 개요\n\n본문"
    assert normalize_section(orig, "본문\n\n> [!DETAIL] x").startswith("## 1. 개요\n\n본문")


def test_figure_placeholders_and_svg_sanitize():
    from knowledgeview import figures
    md = "본문\n\n> [[FIGURE | caption: 캡션 A | search: bpe merge | draw: 그림]]\n\n[[FIGURE | caption: 캡션 B | search: x]]\n"
    reqs = figures.find_requests(md)
    assert [r.caption for _, r in reqs] == ["캡션 A", "캡션 B"]
    out = figures.replace_placeholders(md, ["<figure>A</figure>", None])
    assert "> <figure>A</figure>" in out and "캡션 B" not in out and "[[FIGURE" not in out
    svg = figures.sanitize_svg('앞말 <svg viewBox="0 0 10 10" onload="x()"><script>alert(1)</script>'
                               '<a href="http://evil"><rect width="5" height="5"/></a><use href="#r"/></svg> 뒷말')
    assert "script" not in svg and "onload" not in svg and "evil" not in svg and 'href="#r"' in svg
    with pytest.raises(ValueError):
        figures.sanitize_svg('<!DOCTYPE svg [<!ENTITY a "b">]><svg>&a;</svg>')


class FigureLLM(FakeLLM):
    calls = 0

    def complete(self, system, user):
        FigureLLM.calls += 1
        from knowledgeview import prompts
        if system == prompts.FIGURE_SVG_SYSTEM:
            return '<svg viewBox="0 0 100 50"><rect width="100%" height="100%" fill="#fff"/><text x="5" y="20">그림</text></svg>'
        if "<<<SECTION" in user:
            sec = user.split("<<<SECTION\n", 1)[1].rsplit("\nSECTION>>>", 1)[0]
            head = sec.splitlines()[0].lstrip("# ")
            return sec + f"\n\n> [!DETAIL] 해설\n> 내용\n> [[FIGURE | caption: {head} 그림 | search: concept | draw: 상자 두 개]]"
        return super().complete(system, user)


def test_generate_with_svg_figures_and_cache(tmp_path):
    from knowledgeview.cache import Cache
    root, p = _write_source(tmp_path)
    out = tmp_path / "out"
    settings = {**SETTINGS, "images": {"enabled": True, "web_search": False, "svg_fallback": True, "max_per_section": 1}}
    cache = Cache(tmp_path / "cache")
    FigureLLM.calls = 0
    res = generate(source_path=p, source_root=root, category="", mode="openai", settings=settings,
                   output_dir=out, client=FigureLLM(), cache=cache)
    html = (out / "sample.html").read_text(encoding="utf-8")
    assert html.count('class="kv-figure kv-added"') == 2  # sections 1 and 2; 참고 문헌 skipped
    assert "data:image/svg+xml;base64," in html and "[[FIGURE" not in html
    first_calls = FigureLLM.calls
    assert first_calls == 5  # overview + 2 sections + 2 SVGs
    generate(source_path=p, source_root=root, category="", mode="openai", settings=settings,
             output_dir=out, client=FigureLLM(), cache=cache)
    assert FigureLLM.calls == first_calls  # everything came from the cache
    assert not res["warnings"]


def test_korean_emphasis_after_punctuation():
    from knowledgeview.render import MdRenderer
    r = MdRenderer()
    assert "<strong>선형 분리 불가능 (linearly non-separable)</strong>이라고" in r.render(
        "이를 **선형 분리 불가능 (linearly non-separable)**이라고 한다.")
    assert "<strong>&quot;인용&quot;</strong>을" in r.render('이것은**"인용"**을 보라')
    assert "<strong>보통</strong>" in r.render("**보통** 강조")
    assert "snake_case_name" in r.render("snake_case_name")


def test_regenerate_archives_previous_page(tmp_path):
    root, p = _write_source(tmp_path)
    out = tmp_path / "out"
    settings = {**SETTINGS, "history_keep": 2}
    first = generate(source_path=p, source_root=root, category="a", mode="none", settings=settings, output_dir=out)
    assert first["archived"] is None
    old_html = (out / "a" / "sample.html").read_text(encoding="utf-8")
    import time
    for _ in range(3):
        time.sleep(0.01)
        generate(source_path=p, source_root=root, category="a", mode="none", settings=settings, output_dir=out)
    versions = library.list_history(out, "a/sample.html")
    assert len(versions) == 2  # history_keep
    assert library.list_pages(out)[0]["history"] == 2
    assert [c["path"] for c in library.list_categories(out)] == ["", "a"]  # .history is hidden
    # Restore: the chosen version becomes current, the current page goes to history.
    v = versions[-1]["version"]
    library.restore_history(out, "a/sample.html", v, keep=5)
    assert len(library.list_history(out, "a/sample.html")) == 2
    assert (out / "a" / "sample.html").read_text(encoding="utf-8").startswith(old_html[:200])
    # Moving the page carries its history; deleting it removes the history too.
    new_rel = library.move_page(out, "a/sample.html", "b")
    assert library.history_count(out, new_rel) == 2
    library.delete_page(out, new_rel)
    assert library.history_count(out, new_rel) == 0
    with pytest.raises(PathError):
        library.restore_history(out, new_rel, "../../x", keep=0)


def test_openai_endpoints_settings(tmp_path):
    import json
    from knowledgeview.config import Settings, endpoint_for, mode_ids
    path = tmp_path / "settings.json"
    # A 0.1 settings file with one "openai" block migrates into the endpoint list.
    path.write_text(json.dumps({"default_mode": "openai", "openai": {"base_url": "http://a/v1", "model": "m1", "api_key": "k1"}}),
                    encoding="utf-8")
    s = Settings(path)
    d = s.get()
    assert d["default_mode"] == "openai:default" and d["openai_endpoints"][0]["api_key"] == "k1"
    pub = s.public()
    assert "api_key" not in pub["openai_endpoints"][0] and pub["openai_endpoints"][0]["has_api_key"]
    # Add one endpoint, keep the stored key of the first (empty api_key in the patch).
    s.update({"openai_endpoints": [{"id": "default", "name": "A", "base_url": "http://a/v1/", "model": "m1", "api_key": ""},
                                   {"id": "", "name": "B", "base_url": "http://b/v1", "model": "m2", "api_key": "k2"}]})
    d = s.get()
    assert [e["name"] for e in d["openai_endpoints"]] == ["A", "B"]
    assert d["openai_endpoints"][0]["api_key"] == "k1" and d["openai_endpoints"][0]["base_url"] == "http://a/v1"
    new_id = d["openai_endpoints"][1]["id"]
    assert "openai:" + new_id in mode_ids(d) and endpoint_for("openai:" + new_id, d)["model"] == "m2"
    # Deleting the default endpoint resets the default mode.
    s.update({"openai_endpoints": [d["openai_endpoints"][1]]})
    assert s.get()["default_mode"] == "none"


def test_export_figures_zip(tmp_path):
    import io
    import zipfile
    from knowledgeview.cache import Cache
    from knowledgeview.export import build_zip
    root, p = _write_source(tmp_path)
    out = tmp_path / "out"
    settings = {**SETTINGS, "images": {"enabled": True, "web_search": False, "svg_fallback": True, "max_per_section": 1}}
    generate(source_path=p, source_root=root, category="", mode="openai", settings=settings,
             output_dir=out, client=FigureLLM(), cache=Cache(tmp_path / "cache"))
    data, count = build_zip(out / "sample.html")
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist()
    assert sum(n.startswith("figures/") and n.endswith(".svg") for n in names) == 2
    assert sum(n.startswith("diagrams/") and n.endswith(".mmd") for n in names) == 1
    assert z.read([n for n in names if n.endswith(".mmd")][0]).decode().startswith("graph LR")
    assert z.read([n for n in names if n.endswith(".svg")][0]).startswith(b"<svg")
    listing = z.read("그림_목록.md").decode()
    assert "1. 개요 그림" in listing and "LLM이 생성한 그림" in listing
    assert count == 3


def test_port_free_detects_listener_and_install_config(tmp_path, monkeypatch):
    import socket
    from knowledgeview import config
    from knowledgeview.main import _port_free
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        assert _port_free(port) is False
    assert _port_free(port) is True
    monkeypatch.setattr(config, "APP_ROOT", tmp_path)
    monkeypatch.delenv("KV_PORT", raising=False)
    config.save_install_config({"port": 9123, "data_dir": "x"})
    config.save_install_config({"port": 9124})
    assert config.install_config() == {"port": 9124, "data_dir": "x"}
    assert config.server_address()[1] == 9124
    monkeypatch.setenv("KV_PORT", "9200")
    assert config.server_address()[1] == 9200 and config.port_overridden()


def test_all_modules_import():
    """Modules the tests do not otherwise touch (desktop app, installer) must at least import."""
    import importlib
    import pkgutil

    import knowledgeview
    names = [m.name for m in pkgutil.walk_packages(knowledgeview.__path__, "knowledgeview.")]
    assert "knowledgeview.desktop.installer" in names
    for name in names:
        if name.endswith("__main__"):
            continue
        importlib.import_module(name)
