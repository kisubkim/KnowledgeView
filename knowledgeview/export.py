"""Export a page's figures (embedded data-URI images) and mermaid sources as one ZIP."""

from __future__ import annotations

import base64
import html
import io
import re
import zipfile
from pathlib import Path

from .library import read_meta

_FIGURE = re.compile(r'<figure class="kv-figure[^"]*"[^>]*>(.*?)</figure>', re.S)
_IMG = re.compile(r'<img\b[^>]*\bsrc="data:([\w/+.-]+);base64,([^"]+)"[^>]*>', re.S)
_ALT = re.compile(r'\balt="([^"]*)"')
_CAPTION = re.compile(r"<figcaption>(.*?)</figcaption>", re.S)
_CREDIT = re.compile(r'<span class="kv-credit">(.*?)</span>', re.S)
_LINK = re.compile(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_DIAGRAM_SRC = re.compile(r'<pre class="kv-diagram-src"[^>]*>(.*?)</pre>', re.S)
_HEADING = re.compile(r'<h([23])\b[^>]*>(.*?)</h\1>', re.S)
_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/svg+xml": "svg", "image/webp": "webp", "image/gif": "gif"}


def _text(fragment: str) -> str:
    s = re.sub(r"<[^>]+>", "", fragment or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def _slug(text: str, limit: int = 40) -> str:
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "", text)
    s = re.sub(r"\s+", "_", s).strip("._ ")
    return s[:limit].rstrip("._ ") or "figure"


def _section_at(page: str, pos: int) -> str:
    """Nearest h2/h3 heading text before a position (the anchor '#' is not included)."""
    last = ""
    for m in _HEADING.finditer(page, 0, pos):
        last = _text(m.group(2))
    return last


def build_zip(page_path: Path) -> tuple[bytes, int]:
    """Return (zip bytes, number of files) for one page."""
    page = page_path.read_text(encoding="utf-8", errors="replace")
    meta = read_meta(page_path)
    title = meta.get("title") or page_path.stem
    buf = io.BytesIO()
    lines = [f"# {title} — 그림 목록", ""]
    count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        figs = list(_FIGURE.finditer(page))
        for n, fm in enumerate(figs, 1):
            body = fm.group(1)
            im = _IMG.search(body)
            if not im:
                continue
            mime, data = im.group(1), im.group(2)
            cm = _CAPTION.search(body)
            cap_html = cm.group(1) if cm else ""
            crm = _CREDIT.search(cap_html)
            credit_html = crm.group(1) if crm else ""
            am = _ALT.search(im.group(0))
            caption = _text(_CREDIT.sub("", cap_html)) or (_text(am.group(1)) if am else "")
            kind = "웹 이미지" if 'data-kind="web"' in fm.group(0) else "LLM 생성 SVG"
            name = f"figures/{n:02d}_{_slug(caption)}.{_EXT.get(mime, 'bin')}"
            try:
                zf.writestr(name, base64.b64decode(data))
            except (ValueError, TypeError):
                continue
            count += 1
            section = _section_at(page, fm.start())
            lines += [f"## {n:02d}. {caption}", "", f"- 파일: `{name}`", f"- 종류: {kind}"]
            if section:
                lines.append(f"- 위치: {section}")
            if credit_html:
                lines.append("- 출처: " + re.sub(r"^출처:\s*", "", _text(credit_html)))
                for href, label in _LINK.findall(credit_html):
                    lines.append(f"  - {_text(label)}: {html.unescape(href)}")
            lines.append("")
        diagrams = list(_DIAGRAM_SRC.finditer(page))
        if diagrams:
            lines += ["# mermaid 다이어그램 원본", "",
                      "`diagrams/*.mmd`는 mermaid 원본입니다. https://mermaid.live 같은 편집기에서 열거나, "
                      "페이지에서 다이어그램을 눌러 연 뷰어의 저장 버튼으로 SVG/PNG를 받을 수 있습니다.", ""]
        for n, dm in enumerate(diagrams, 1):
            section = _section_at(page, dm.start())
            name = f"diagrams/{n:02d}_{_slug(section or 'diagram')}.mmd"
            zf.writestr(name, html.unescape(dm.group(1)))
            count += 1
            lines.append(f"- `{name}`" + (f" — {section}" if section else ""))
        if not count:
            lines.append("이 페이지에는 내보낼 그림이 없습니다.")
        zf.writestr("그림_목록.md", "\n".join(lines) + "\n")
    return buf.getvalue(), count
