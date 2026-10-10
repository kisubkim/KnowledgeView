"""Split a Markdown document into title, metadata, intro and level-2 sections.

Sections are the unit of LLM enrichment, so splitting must never cut inside a
fenced code block (a ``# comment`` in a bash block is not a heading).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_META_LINE = re.compile(r"^>\s*\*\*(.+?)\*\*\s*[:：]\s*(.*)$")
_FRONT_KV = re.compile(r"^([A-Za-z0-9_\-가-힣 ]+)\s*:\s*(.*)$")


@dataclass
class Section:
    heading: str  # heading text without the leading "## "
    markdown: str  # full section markdown, heading line included

    @property
    def mermaid_count(self) -> int:
        return count_mermaid(self.markdown)


@dataclass
class Document:
    title: str
    meta: list[tuple[str, str]] = field(default_factory=list)
    intro: str = ""
    sections: list[Section] = field(default_factory=list)


def count_mermaid(md: str) -> int:
    return sum(1 for line in md.splitlines() if re.match(r"^\s*(>\s*)*(`{3,}|~{3,})\s*mermaid\b", line))


def iter_unfenced(lines: list[str]):
    """Yield (index, line, in_fence) for each line."""
    fence: str | None = None
    for i, line in enumerate(lines):
        m = _FENCE.match(line)
        if fence is None:
            if m:
                fence = m.group(1)
                yield i, line, True
                continue
            yield i, line, False
        else:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not line.strip()[len(m.group(1)):].strip():
                fence = None
            yield i, line, True


def parse(md: str, fallback_title: str) -> Document:
    md = md.replace("\r\n", "\n").lstrip("﻿")
    lines = md.split("\n")
    meta: list[tuple[str, str]] = []

    # YAML-ish front matter (simple "key: value" lines only).
    if lines and lines[0].strip() == "---":
        for j in range(1, min(len(lines), 60)):
            if lines[j].strip() == "---":
                for kv in lines[1:j]:
                    m = _FRONT_KV.match(kv.strip())
                    if m and m.group(2).strip():
                        meta.append((m.group(1).strip(), m.group(2).strip().strip("\"'")))
                lines = lines[j + 1:]
                break

    title = ""
    h2_idx: list[int] = []
    h1_idx = None
    for i, line, in_fence in iter_unfenced(lines):
        if in_fence:
            continue
        if line.startswith("# ") and h1_idx is None and not h2_idx:
            h1_idx = i
            title = line[2:].strip()
        elif line.startswith("## "):
            h2_idx.append(i)

    start = h1_idx + 1 if h1_idx is not None else 0
    end_intro = h2_idx[0] if h2_idx else len(lines)
    intro_lines = lines[start:end_intro]

    # Leading "> **key**: value" block right after the title becomes header metadata.
    k = 0
    while k < len(intro_lines) and not intro_lines[k].strip():
        k += 1
    j = k
    block_meta = []
    while j < len(intro_lines) and intro_lines[j].startswith(">"):
        m = _META_LINE.match(intro_lines[j].strip())
        if not m:
            block_meta = []
            break
        block_meta.append((m.group(1).strip(), m.group(2).strip()))
        j += 1
    if block_meta:
        meta.extend(block_meta)
        intro_lines = intro_lines[j:]

    intro = _strip_rules("\n".join(intro_lines))

    sections = []
    bounds = h2_idx + [len(lines)]
    for a, b in zip(bounds, bounds[1:]):
        body = _strip_rules("\n".join(lines[a:b]), leading=False)
        sections.append(Section(heading=lines[a][3:].strip(), markdown=body))

    return Document(title=title or fallback_title, meta=meta, intro=intro, sections=sections)


def _strip_rules(md: str, leading: bool = True) -> str:
    """Drop blank lines and '---' separators at the edges (the template draws its own)."""
    ls = md.split("\n")

    def junk(s: str) -> bool:
        return not s.strip() or s.strip() in ("---", "***", "___")

    while ls and junk(ls[-1]):
        ls.pop()
    if leading:
        while ls and junk(ls[0]):
            ls.pop(0)
    return "\n".join(ls)


def join(doc: Document) -> str:
    """Rebuild Markdown (used to feed whole-document prompts)."""
    parts = [f"# {doc.title}"]
    if doc.intro:
        parts.append(doc.intro)
    parts += [s.markdown for s in doc.sections]
    return "\n\n".join(parts)
