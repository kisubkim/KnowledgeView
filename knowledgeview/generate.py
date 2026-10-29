"""One Markdown file -> one HTML page in the output library.

Modes:
- ``none``: no LLM; the source is restructured by the template only.
- ``claude_cli`` / ``codex_cli`` / ``openai:<endpoint id>``: each level-2 section is enriched separately (callouts added
  around the untouched original), plus one whole-document call for the overview.
  A section whose LLM output fails validation falls back to the original text.
"""

from __future__ import annotations

import json
import hashlib
import os
import posixpath
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Callable

from . import cache as cache_mod
from . import figures, mdparse, prompts
from .config import endpoint_for, mode_label
from .library import PathError, archive_page, clean_rel, default_category, join_inside, read_meta, sha256_of
from .llm import Cancelled, LLMClient, LLMError, make_client
from .mdparse import Document, iter_unfenced
from .render import build_page

OVERVIEW_MAX_CHARS = 60_000
SECTION_RETRIES = 1

Progress = Callable[[int, int, str], None]


def _noop(done: int, total: int, msg: str) -> None:
    pass


def _strip_llm_wrapping(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    m = re.match(r"^```(?:markdown|md)?\s*\n(.*)\n```\s*$", text, re.S)
    if m:
        text = m.group(1)
    text = re.sub(r"^<<<SECTION\s*\n|\nSECTION>>>\s*$", "", text)
    return text.strip()


def normalize_section(original: str, enriched: str) -> str:
    """Drop a chatty preamble before the section heading, or restore a dropped heading."""
    first = original.split("\n", 1)[0].strip()
    if not first.startswith("## "):
        return enriched
    lines = enriched.split("\n")
    for i, line, fenced in iter_unfenced(lines):
        if not fenced and line.strip() == first:
            return "\n".join(lines[i:])
    if not re.match(r"^#{1,6} ", lines[0].strip()):
        return first + "\n\n" + enriched
    return enriched


def _headings(md: str) -> list[str]:
    return [line.strip() for _, line, fenced in iter_unfenced(md.split("\n")) if not fenced and re.match(r"^#{1,6} ", line)]


def validate_section(original: str, enriched: str) -> str | None:
    """Return a reason when the enriched section dropped or reshaped original content."""
    if not enriched.strip():
        return "빈 응답"
    if mdparse.count_mermaid(enriched) < mdparse.count_mermaid(original):
        return "mermaid 다이어그램이 빠짐"
    orig_h, new_h = _headings(original), _headings(enriched)
    if orig_h and (not new_h or new_h[0] != orig_h[0]):
        return "섹션 제목이 바뀜"
    missing = [h for h in orig_h if h not in new_h]
    if missing:
        return f"원문 제목 누락: {missing[0]}"
    if len(enriched) < len(original) * 0.9:
        return "응답이 원문보다 짧음 (원문 일부 누락 의심)"
    return None


def _parse_overview(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("JSON을 찾지 못함")
    raw = json.loads(text[a:b + 1])
    out: dict = {}
    if isinstance(raw.get("summary"), str):
        out["summary"] = raw["summary"].strip()
    for key in ("objectives", "key_points", "prerequisites"):
        vals = raw.get(key)
        if isinstance(vals, list):
            out[key] = [str(v).strip() for v in vals if str(v).strip()]
    gl = raw.get("glossary")
    if isinstance(gl, list):
        out["glossary"] = [{"term": str(g.get("term", "")).strip(), "desc": str(g.get("desc", "")).strip()}
                           for g in gl if isinstance(g, dict) and g.get("term")]
    qz = raw.get("quiz")
    if isinstance(qz, list):
        out["quiz"] = [{"q": str(q.get("q", "")).strip(), "a": str(q.get("a", "")).strip()}
                       for q in qz if isinstance(q, dict) and q.get("q")]
    return out


def heuristic_overview(doc: Document) -> dict:
    """Overview without an LLM: collect the document's own '핵심 포인트' style quotes."""
    points = []
    for md in [doc.intro] + [s.markdown for s in doc.sections]:
        for m in re.finditer(r"^>\s*\*\*(?:핵심[^*]*|요점|Key[^*]*)\*\*\s*[:：]?\s*(.+)$", md, re.M | re.I):
            points.append(re.sub(r"\*\*|`", "", m.group(1)).strip())
    return {"key_points": points[:10]} if points else {}


def _save_failure(folder: Path | None, stem: str, index: int, reason: str, raw: str) -> None:
    """Keep rejected LLM answers for diagnosis (best effort)."""
    if folder is None:
        return
    try:
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{datetime.now():%Y%m%d-%H%M%S}_{stem}_s{index + 1}.md"
        (folder / name).write_text(f"<!-- 거부 사유: {reason} -->\n{raw}", encoding="utf-8")
    except OSError:
        pass


def _outline(doc: Document) -> str:
    return "\n".join(f"- {s.heading}" for s in doc.sections)


def _skip(heading: str, skip_headings: list[str]) -> bool:
    return any(k and k.lower() in heading.lower() for k in skip_headings)


def _mode_key(mode: str | None, settings: dict) -> str:
    if not mode:
        return ""
    ep = endpoint_for(mode, settings)
    return "openai:" + ep["id"] if ep else mode


def find_mode_page(output_dir: Path, category: PurePosixPath, source_path: Path, mode: str,
                   settings: dict) -> PurePosixPath | None:
    """Find a current page of this source and mode, including legacy or moved filenames."""
    folder = join_inside(output_dir, category)
    for page in sorted(folder.glob("*.html")):
        meta = read_meta(page)
        src = meta.get("source_path")
        if (src and Path(src).resolve() == source_path.resolve()
                and _mode_key(meta.get("mode"), settings) == _mode_key(mode, settings)):
            return category / page.name
    return None


def output_page_rel(output_dir: Path, category: PurePosixPath, source_path: Path, mode: str,
                    settings: dict) -> PurePosixPath:
    existing = find_mode_page(output_dir, category, source_path, mode, settings)
    if existing:
        return existing
    canonical = category / (source_path.stem + ".html")
    if not join_inside(output_dir, canonical).exists():
        return canonical
    suffix = {"none": "structured", "claude_cli": "claude", "codex_cli": "codex"}.get(mode)
    if not suffix:
        suffix = "openai-" + hashlib.sha256(_mode_key(mode, settings).encode()).hexdigest()[:12]
    candidate = category / f"{source_path.stem}--{suffix}.html"
    # A different source can have the same stem or the literal '--codex' suffix.
    number = 2
    while join_inside(output_dir, candidate).exists():
        candidate = category / f"{source_path.stem}--{suffix}-{number}.html"
        number += 1
    return candidate


def make_link_resolver(source_path: Path, source_root: Path, out_rel: PurePosixPath,
                       *, output_dir: Path | None = None, mode: str = "none", settings: dict | None = None):
    """Map links to other .md files in the same source root to their mirrored output page."""
    root = source_root.resolve()

    def resolve(href: str) -> str | None:
        path, _, frag = href.partition("#")
        target = (source_path.parent / path).resolve()
        if root not in target.parents:
            return None
        trel = target.relative_to(root).as_posix()
        dest = PurePosixPath(default_category(trel)) / (target.stem + ".html")
        if output_dir is not None:
            dest = find_mode_page(output_dir, dest.parent, target, mode, settings or {}) or dest
        link = posixpath.relpath(dest.as_posix(), out_rel.parent.as_posix() or ".")
        return link + (f"#{frag}" if frag else "")

    return resolve


def generate(
    *,
    source_path: Path,
    source_root: Path,
    category: str,
    mode: str,
    settings: dict,
    output_dir: Path,
    progress: Progress = _noop,
    cancel: threading.Event | None = None,
    client: LLMClient | None = None,
    failures_dir: Path | None = None,
    cache: cache_mod.Cache | None = None,
    output_rel: str | None = None,
) -> dict:
    cancel = cancel or threading.Event()
    cat = clean_rel(category)
    out_rel = clean_rel(output_rel, allow_empty=False) if output_rel else output_page_rel(
        output_dir, cat, source_path, mode, settings)
    out_path = join_inside(output_dir, out_rel)
    if out_rel.parent != cat or out_rel.suffix.lower() != ".html":
        raise PathError("출력 페이지의 카테고리 또는 확장자가 잘못되었습니다.")
    if out_path.exists():
        previous = read_meta(out_path)
        if (not previous.get("source_path") or Path(previous["source_path"]).resolve() != source_path.resolve()
                or _mode_key(previous.get("mode"), settings) != _mode_key(mode, settings)):
            raise PathError("다른 원본 또는 생성 방식의 페이지는 덮어쓸 수 없습니다.")

    text = source_path.read_text(encoding="utf-8-sig", errors="replace")
    doc = mdparse.parse(text, fallback_title=source_path.stem)
    sections_md = [s.markdown for s in doc.sections]
    flags: list[dict] = [{} for _ in sections_md]
    warnings: list[str] = []
    overview: dict = {}
    fig_counts: dict[str, int] = {}
    cache = cache or cache_mod.Cache(None)
    model = ""
    reasoning_effort = ""

    if mode == "none":
        progress(0, 1, "페이지 구성 중")
        overview = heuristic_overview(doc)
    else:
        client = client or make_client(mode, settings, cancel)
        model = client.model or ("기본 모델" if mode in ("claude_cli", "codex_cli") else "")
        reasoning_effort = getattr(client, "reasoning_effort", "") if mode == "codex_cli" else ""
        cache_model = {"model": client.model, "reasoning_effort": reasoning_effort} if mode == "codex_cli" else client.model
        if mode in ("claude_cli", "codex_cli"):
            cfg = settings.get("claude" if mode == "claude_cli" else "codex", {})
        else:
            cfg = endpoint_for(mode, settings) or {}
        workers = max(1, int(cfg.get("concurrency") or 1))
        targets = [i for i, s in enumerate(doc.sections) if not _skip(s.heading, settings.get("skip_headings", []))]
        total = len(targets) + 1
        done = 0
        lock = threading.Lock()
        progress(0, total, f"LLM 상세화 시작 (섹션 {len(targets)}개 + 개요, 동시 {workers}개)")

        def tick(msg: str) -> None:
            nonlocal done
            with lock:
                done += 1
                progress(done, total, msg)

        img_cfg = settings.get("images") or {}
        images_on = bool(img_cfg.get("enabled")) and (img_cfg.get("web_search") or img_cfg.get("svg_fallback"))
        max_figs = max(0, int(img_cfg.get("max_per_section") or 0))
        section_system = prompts.SECTION_SYSTEM
        if images_on and max_figs:
            section_system += prompts.SECTION_FIGURE_RULE.format(max_figures=max_figs)

        def do_section(i: int) -> None:
            sec = doc.sections[i]
            user = prompts.SECTION_USER.format(title=doc.title, outline=_outline(doc), index=i + 1,
                                               total=len(doc.sections), section=sec.markdown)
            ckey = cache_mod.key("section", mode, cache_model, section_system, user)
            cached = cache.get("sections", ckey)
            if isinstance(cached, str):
                sections_md[i] = cached
                flags[i] = {"enriched": True}
                tick(f"섹션 완료(캐시): {sec.heading}")
                return
            out, reason = "", ""
            for attempt in range(1 + SECTION_RETRIES):
                if attempt:
                    progress(done, total, f"재시도: {sec.heading} ({reason})")
                try:
                    raw = client.complete(section_system, user)
                except LLMError as e:
                    out, reason = "", f"LLM 오류: {e}"
                    continue
                out = normalize_section(sec.markdown, _strip_llm_wrapping(raw))
                reason = validate_section(sec.markdown, out)
                if not reason:
                    cache.put("sections", ckey, out)
                    break
                _save_failure(failures_dir, source_path.stem, i, reason, raw)
            if reason:
                flags[i] = {"warning": f"보강 실패로 원문을 그대로 표시합니다 ({reason})"}
                warnings.append(f"섹션 '{sec.heading}': {reason}")
                tick(f"섹션 원문 유지: {sec.heading} ({reason})")
            else:
                sections_md[i] = out
                flags[i] = {"enriched": True}
                tick(f"섹션 완료: {sec.heading}")

        def do_overview() -> None:
            nonlocal overview
            user = prompts.OVERVIEW_USER.format(doc=mdparse.join(doc)[:OVERVIEW_MAX_CHARS])
            ckey = cache_mod.key("overview", mode, cache_model, prompts.OVERVIEW_SYSTEM, user)
            cached = cache.get("overview", ckey)
            if isinstance(cached, dict):
                overview = cached
                tick("개요 완료(캐시)")
                return
            try:
                overview = _parse_overview(client.complete(prompts.OVERVIEW_SYSTEM, user))
                cache.put("overview", ckey, overview)
                tick("개요(요약·용어 사전·확인 문제) 완료")
            except (LLMError, ValueError) as e:
                overview = heuristic_overview(doc)
                warnings.append(f"개요 생성 실패: {e}")
                tick(f"개요 생성 실패: {e}")

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(do_overview)] + [pool.submit(do_section, i) for i in targets]
            for f in futures:
                try:
                    f.result()
                except Cancelled:
                    cancel.set()
        if cancel.is_set():
            raise Cancelled()
        if targets and all("warning" in flags[i] for i in targets):
            first = next((w for w in warnings if w.startswith("섹션")), "")
            raise LLMError(f"모든 섹션의 보강이 실패했습니다. {first}")

        # Figures: resolve the placeholders the sections asked for (web image, else SVG).
        jobs: list[tuple[int, int, figures.FigureRequest | None]] = []
        for i in targets:
            for n, (_, req) in enumerate(figures.find_requests(sections_md[i])):
                jobs.append((i, n, req if (images_on and n < max_figs) else None))
        results: dict[tuple[int, int], str | None] = {}
        todo = [j for j in jobs if j[2] is not None]
        if todo:
            total += len(todo)
            progress(done, total, f"그림 {len(todo)}개 준비 (웹 검색 → 없으면 SVG)")

        def do_figure(i: int, n: int, req: figures.FigureRequest) -> None:
            fkey = cache_mod.key("figure", mode, cache_model, req.as_dict(), bool(img_cfg.get("web_search")),
                                 bool(img_cfg.get("svg_fallback")), prompts.FIGURE_PICK_SYSTEM, prompts.FIGURE_SVG_SYSTEM)
            fig = cache.get("figures", fkey)
            from_cache = isinstance(fig, dict)
            if not from_cache:
                errors: list[str] = []

                def log(m: str) -> None:
                    if "실패" in m:
                        errors.append(m)
                    progress(done, total, m)

                fig = figures.resolve(req, client=client, cfg=img_cfg, context=sections_md[i], log=log)
                if fig:
                    cache.put("figures", fkey, fig)
                elif errors:
                    warnings.append(f"그림 생략 '{req.caption[:40]}': {errors[-1][:200]}")
            results[(i, n)] = figures.figure_html(fig) if fig else None
            if fig:
                fig_counts[fig["kind"]] = fig_counts.get(fig["kind"], 0) + 1
            kind = {"web": "웹 이미지", "svg": "SVG"}.get(fig["kind"]) if fig else "없음"
            tick(f"그림 {kind}{'(캐시)' if from_cache else ''}: {req.caption}")

        if todo:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(do_figure, i, n, req) for i, n, req in todo]
                for f in futures:
                    try:
                        f.result()
                    except Cancelled:
                        cancel.set()
            if cancel.is_set():
                raise Cancelled()
        for i in targets:
            count = len(figures.find_requests(sections_md[i]))
            if count:
                sections_md[i] = figures.replace_placeholders(sections_md[i], [results.get((i, n)) for n in range(count)])

    try:
        source_rel = source_path.resolve().relative_to(source_root.resolve()).as_posix()
    except ValueError:
        source_rel = source_path.name
    info = {
        "mode": mode,
        "mode_label": mode_label(mode, settings),
        "model": model,
        "reasoning_effort": reasoning_effort,
        "category": cat.as_posix().strip("."),
        "source_path": str(source_path),
        "source_root": str(source_root),
        "source_rel": source_rel,
        "source_sha256": sha256_of(source_path),
        "warnings": warnings,
        "figures": fig_counts,
    }
    page = build_page(
        doc, sections_md, info=info, overview=overview, section_flags=flags,
        source_dir=source_path.parent,
        link_resolver=make_link_resolver(source_path, source_root, out_rel, output_dir=output_dir,
                                         mode=mode, settings=settings),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_name(out_path.name + ".tmp")
    tmp.write_text(page, encoding="utf-8")
    # The page being replaced goes to <out>/.history/ (only now, after the new page is ready).
    archived = None
    if out_path.is_file():
        archived = archive_page(output_dir, out_rel, keep=int(settings.get("history_keep") or 0))
    os.replace(tmp, out_path)
    return {"rel": out_rel.as_posix(), "warnings": warnings, "archived": archived}
