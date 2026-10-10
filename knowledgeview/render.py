"""Markdown -> HTML fragments, and fragments -> one self-contained page via the Jinja2 template.

The page carries its own CSS and JS. Rendering libraries (mermaid) are not inlined:
the page loads them from the app (``asset_base``), so pages stay small.
"""

from __future__ import annotations

import base64
import html
import json
import mimetypes
import re
from datetime import datetime
from pathlib import Path
from typing import Callable

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup
from markdown_it import MarkdownIt
from markdown_it.common.utils import isMdAsciiPunct, isPunctChar, isWhiteSpace
from markdown_it.rules_inline.state_inline import Scanned, StateInline
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.util import ClassNotFound

from . import __version__
from .config import PACKAGE_DIR
from .mdparse import Document

# GitHub-style alerts plus the types the enrichment prompt uses for added content.
CALLOUTS = {
    "NOTE": ("note", "참고", False),
    "TIP": ("tip", "팁", False),
    "IMPORTANT": ("important", "중요", False),
    "WARNING": ("warning", "주의", False),
    "CAUTION": ("caution", "경고", False),
    "DETAIL": ("detail", "상세 해설", True),
    "EXAMPLE": ("example", "예시", True),
    "ANALOGY": ("analogy", "비유로 이해하기", True),
    "PITFALL": ("pitfall", "흔한 오해", True),
    "SUMMARY": ("summary", "섹션 요약", True),
}
# Bold labels at the start of an ordinary blockquote ("> **핵심 포인트**: ...").
LABEL_CALLOUTS = [
    (re.compile(r"핵심|요점|Key", re.I), "key"),
    (re.compile(r"주의|경고|Warning|Caution", re.I), "warning"),
    (re.compile(r"팁|Tip", re.I), "tip"),
    (re.compile(r"비유|예시|Example", re.I), "example"),
    (re.compile(r"Note|참고|노트", re.I), "note"),
]
_ALERT = re.compile(r"^\[!([A-Za-z]+)\][ \t]*")
_IMG_MAX = 5 * 1024 * 1024

_formatter = HtmlFormatter(nowrap=True)
PYGMENTS_CSS = HtmlFormatter(style="friendly").get_style_defs(".hl")
PYGMENTS_CSS_DARK = HtmlFormatter(style="monokai").get_style_defs("[data-theme=dark] .hl")

_env = Environment(
    loader=FileSystemLoader(str(PACKAGE_DIR / "templates")),
    autoescape=select_autoescape(["html", "j2"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


_CJK = re.compile(r"[ᄀ-ᇿ぀-ヿ㄰-㆏㐀-䶿一-鿿가-힯＀-￯]")
_orig_scan_delims = StateInline.scanDelims


def _cjk_scan_delims(self, start: int, canSplitWord: bool) -> Scanned:
    """CommonMark does not close '**bold (x)**이라고': ')' then a Hangul letter breaks flanking.

    For '*' runs, treat an adjacent CJK character like punctuation (as markdown-it's
    cjk-friendly variants do), so Korean particles right after emphasis work."""
    res = _orig_scan_delims(self, start, canSplitWord)
    if self.src[start] != "*" or (res.can_open and res.can_close):
        return res
    pos = start
    while pos < self.posMax and self.src[pos] == "*":
        pos += 1
    last = self.src[start - 1] if start > 0 else " "
    nxt = self.src[pos] if pos < self.posMax else " "
    if not (_CJK.match(last) or _CJK.match(nxt)):
        return res

    def punct(c: str) -> bool:
        return bool(_CJK.match(c)) or isMdAsciiPunct(ord(c)) or isPunctChar(c)

    lp, np_, lw, nw = punct(last), punct(nxt), isWhiteSpace(ord(last)), isWhiteSpace(ord(nxt))
    left = not (nw or (np_ and not (lw or lp)))
    right = not (lw or (lp and not (nw or np_)))
    return Scanned(left, right, pos - start)


StateInline.scanDelims = _cjk_scan_delims


def slugify(text: str) -> str:
    s = re.sub(r"<[^>]+>", "", text).strip().lower()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.U)
    s = re.sub(r"[\s_]+", "-", s).strip("-")
    return s or "section"


class MdRenderer:
    """One instance per page: heading ids stay unique across intro and sections."""

    def __init__(self, source_dir: Path | None = None, link_resolver: Callable[[str], str | None] | None = None):
        self.source_dir = source_dir
        self.link_resolver = link_resolver
        self.used_ids: dict[str, int] = {}
        self.toc: list[dict] = []
        self.stats = {"diagrams": 0, "tables": 0, "code": 0}
        md = MarkdownIt("commonmark", {"html": True, "typographer": False})
        md.enable(["table", "strikethrough"])
        md.use(footnote_plugin).use(tasklists_plugin).use(deflist_plugin)
        md.core.ruler.after("inline", "kv_transform", self._transform)
        # markdown-it-py passes the renderer as the first argument of a render rule.
        def rule(fn):
            return lambda _renderer, tokens, idx, options, env: fn(tokens, idx, options, env)

        md.add_render_rule("fence", rule(self._fence))
        md.add_render_rule("code_block", rule(self._fence))
        md.add_render_rule("blockquote_open", rule(self._bq_open))
        md.add_render_rule("blockquote_close", rule(self._bq_close))
        md.add_render_rule("table_open", lambda *a: '<div class="kv-table"><table>\n')
        md.add_render_rule("table_close", lambda *a: "</table></div>\n")
        self.md = md

    # -- token rewriting ------------------------------------------------------

    def _unique_id(self, base: str) -> str:
        n = self.used_ids.get(base, 0)
        self.used_ids[base] = n + 1
        return base if n == 0 else f"{base}-{n}"

    def _transform(self, state) -> None:
        tokens = state.tokens
        stack = []
        for i, tok in enumerate(tokens):
            if tok.type == "heading_open":
                inline = tokens[i + 1]
                text = inline.content
                hid = self._unique_id(slugify(text))
                tok.attrSet("id", hid)
                level = int(tok.tag[1])
                if level in (2, 3):
                    plain = re.sub(r"[*_`]", "", text)
                    self.toc.append({"level": level, "id": hid, "text": plain})
            elif tok.type == "blockquote_open":
                info = self._callout_info(tokens, i, state)
                tok.meta = info or {}
                stack.append(tok)
            elif tok.type == "blockquote_close":
                tok.meta = stack.pop().meta if stack else {}
            elif tok.type == "table_open":
                self.stats["tables"] += 1
            elif tok.type == "inline" and tok.children:
                for child in tok.children:
                    if child.type == "link_open":
                        self._rewrite_link(child)
                    elif child.type == "image":
                        self._embed_image(child)

    def _callout_info(self, tokens, i: int, state) -> dict | None:
        if i + 2 >= len(tokens) or tokens[i + 1].type != "paragraph_open":
            return None
        inline = tokens[i + 2]
        children = inline.children or []
        m = _ALERT.match(inline.content)
        if m and m.group(1).upper() in CALLOUTS and children and children[0].type == "text":
            kind, label, added = CALLOUTS[m.group(1).upper()]
            # Title = rest of the first line; body = the remaining lines of the paragraph.
            first = children[0]
            first.content = _ALERT.sub("", first.content, count=1)
            cut = next((k for k, c in enumerate(children) if c.type in ("softbreak", "hardbreak")), len(children))
            title_children = [c for c in children[:cut] if not (c.type == "text" and not c.content)]
            title = self.md.renderer.renderInline(title_children, self.md.options, state.env).strip() if title_children else ""
            inline.children = children[cut + 1:]
            if not inline.children:
                tokens[i + 1].hidden = True
                tokens[i + 3].hidden = True
            return {"kind": kind, "title": title or html.escape(label), "added": added}
        lead = [c for c in children if not (c.type == "text" and not c.content)]
        if len(lead) > 1 and lead[0].type == "strong_open":
            label = lead[1].content
            for rx, kind in LABEL_CALLOUTS:
                if rx.search(label):
                    return {"kind": kind, "title": "", "added": False}
        return None

    def _rewrite_link(self, tok) -> None:
        href = tok.attrGet("href") or ""
        if self.link_resolver and re.search(r"\.md(#.*)?$", href, re.I) and not re.match(r"^[a-z]+:", href, re.I):
            new = self.link_resolver(href)
            if new:
                tok.attrSet("href", new)
            else:
                tok.attrSet("class", "kv-dead-link")
                tok.attrSet("title", "변환되지 않은 문서: " + href)
        elif re.match(r"^https?:", href, re.I):
            tok.attrSet("target", "_blank")
            tok.attrSet("rel", "noopener")

    def _embed_image(self, tok) -> None:
        """Inline local images as data URIs so the page stays a single file."""
        src = tok.attrGet("src") or ""
        if not self.source_dir or re.match(r"^(?:[a-z]+:|/|#)", src, re.I):
            return
        p = (self.source_dir / src.split("#")[0].split("?")[0]).resolve()
        if p.is_file() and p.stat().st_size <= _IMG_MAX:
            mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
            tok.attrSet("src", f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}")

    # -- render rules ---------------------------------------------------------

    def _fence(self, tokens, idx, options, env) -> str:
        tok = tokens[idx]
        lang = (tok.info or "").strip().split(maxsplit=1)[0].lower() if tok.info else ""
        code = tok.content
        if lang == "mermaid":
            self.stats["diagrams"] += 1
            return (
                '<figure class="kv-diagram"><div class="kv-diagram-tools">'
                '<button type="button" class="kv-zoom" title="크게 보기">⤢</button>'
                '<button type="button" class="kv-src" title="다이어그램 원본">&lt;/&gt;</button></div>'
                f'<pre class="mermaid">{html.escape(code)}</pre>'
                f'<pre class="kv-diagram-src" hidden>{html.escape(code)}</pre></figure>\n'
            )
        self.stats["code"] += 1
        try:
            lexer = get_lexer_by_name(lang) if lang else TextLexer()
        except ClassNotFound:
            lexer = TextLexer()
        body = highlight(code, lexer, _formatter)
        label = html.escape(lang or "text")
        return (
            f'<div class="kv-code"><div class="kv-code-bar"><span>{label}</span>'
            '<button type="button" class="kv-copy">복사</button></div>'
            f'<pre class="hl"><code>{body}</code></pre></div>\n'
        )

    def _bq_open(self, tokens, idx, options, env) -> str:
        info = tokens[idx].meta or {}
        if not info:
            return "<blockquote>\n"
        cls = f"kv-callout kv-c-{info['kind']}" + (" kv-added" if info.get("added") else "")
        title = f'<div class="kv-callout-title">{info["title"]}</div>' if info.get("title") else ""
        return f'<div class="{cls}">{title}<div class="kv-callout-body">\n'

    def _bq_close(self, tokens, idx, options, env) -> str:
        return "</div></div>\n" if tokens[idx].meta else "</blockquote>\n"

    def render(self, markdown: str) -> str:
        return self.md.render(markdown)


def build_page(
    doc: Document,
    sections_md: list[str],
    *,
    info: dict,
    overview: dict | None = None,
    section_flags: list[dict] | None = None,
    source_dir: Path | None = None,
    link_resolver: Callable[[str], str | None] | None = None,
    asset_base: str = "/assets",
) -> str:
    r = MdRenderer(source_dir=source_dir, link_resolver=link_resolver)
    intro_html = r.render(doc.intro) if doc.intro else ""
    sections = []
    for k, md_text in enumerate(sections_md):
        flags = (section_flags or [{}] * len(sections_md))[k]
        sections.append({"html": r.render(md_text), **flags})

    # Reading time counts text only: embedded figures are long base64 data URIs.
    total_chars = sum(len(re.sub(r"<figure\b.*?</figure>", "", s, flags=re.S)) for s in [doc.intro, *sections_md])
    meta = {
        **info,
        "title": doc.title,
        "generator": f"KnowledgeView {__version__}",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    ov = overview or {}
    return _env.get_template("page.html.j2").render(
        title=doc.title,
        doc_meta=[(k, Markup(r.md.renderInline(v))) for k, v in doc.meta],
        intro_html=intro_html,
        sections=sections,
        toc=r.toc,
        overview=ov,
        stats={**r.stats, "minutes": max(1, round(total_chars / 600))},
        info=meta,
        meta_json=json.dumps(meta, ensure_ascii=False).replace("</", "<\\/"),
        asset_base=asset_base.rstrip("/"),
        pygments_css=PYGMENTS_CSS + "\n" + PYGMENTS_CSS_DARK,
    )
