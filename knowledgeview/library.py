"""Source Markdown trees (read-only) and the output library of generated pages.

The output library is plain folders: a category is a folder under the output dir
(nested categories are nested folders) and a page is one ``.html`` file whose
metadata is embedded in a ``<script id="kv-meta">`` JSON block in its <head>.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path, PurePosixPath

META_RE = re.compile(r'<script type="application/json" id="kv-meta">(.*?)</script>', re.S)
_BAD_CHARS = re.compile(r'[<>:"|?*\x00-\x1f\\]')


class PathError(ValueError):
    pass


def clean_rel(rel: str | None, *, allow_empty: bool = True) -> PurePosixPath:
    """Validate a user-supplied relative path ("a/b/c"); reject traversal and odd names."""
    rel = (rel or "").replace("\\", "/").strip().strip("/")
    if not rel:
        if allow_empty:
            return PurePosixPath("")
        raise PathError("경로가 비어 있습니다.")
    parts = []
    for part in rel.split("/"):
        part = part.strip()
        if not part or part in (".", "..") or _BAD_CHARS.search(part) or part.endswith("."):
            raise PathError(f"허용되지 않는 경로입니다: {rel}")
        parts.append(part)
    return PurePosixPath(*parts)


def join_inside(base: Path, rel: PurePosixPath | str) -> Path:
    base = base.resolve()
    target = (base / str(rel)).resolve()
    if target != base and base not in target.parents:
        raise PathError("기준 폴더 밖의 경로입니다.")
    return target


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- sources


def list_sources(roots: list[Path]) -> list[dict]:
    """All ``*.md`` files under each root, as {root, rel, ...} entries."""
    out = []
    for idx, root in enumerate(roots):
        entry = {"index": idx, "root": str(root), "exists": root.is_dir(), "files": []}
        if root.is_dir():
            for p in sorted(root.rglob("*.md")):
                if any(part.startswith(".") for part in p.relative_to(root).parts):
                    continue
                st = p.stat()
                entry["files"].append({
                    "rel": p.relative_to(root).as_posix(),
                    "path": str(p),
                    "size": st.st_size,
                    "mtime": _iso(st.st_mtime),
                })
        out.append(entry)
    return out


def resolve_source(roots: list[Path], index: int, rel: str) -> Path:
    if not 0 <= index < len(roots):
        raise PathError("원본 루트 번호가 잘못되었습니다.")
    path = join_inside(roots[index], clean_rel(rel, allow_empty=False))
    if path.suffix.lower() != ".md" or not path.is_file():
        raise PathError(f"MD 파일이 없습니다: {rel}")
    return path


def default_category(rel: str) -> str:
    """Mirror the source folder structure: 'a/b/x.md' -> 'a/b'."""
    return PurePosixPath(rel.replace("\\", "/")).parent.as_posix().strip(".")


# --------------------------------------------------------------------------- output library


def read_meta(page: Path) -> dict:
    try:
        with page.open("r", encoding="utf-8", errors="replace") as f:
            head = f.read(64 * 1024)
    except OSError:
        return {}
    m = META_RE.search(head)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except ValueError:
        return {}


def list_categories(out: Path) -> list[dict]:
    cats = [{"path": "", "pages": len(list(out.glob("*.html")))}]
    for d in sorted(p for p in out.rglob("*") if p.is_dir()):
        rel = d.relative_to(out)
        if any(part.startswith(".") for part in rel.parts):
            continue
        cats.append({"path": rel.as_posix(), "pages": len(list(d.glob("*.html")))})
    return cats


def list_pages(out: Path) -> list[dict]:
    pages = []
    for p in sorted(out.rglob("*.html")):
        rel = p.relative_to(out)
        if any(part.startswith(".") for part in rel.parts):
            continue
        meta = read_meta(p)
        src = meta.get("source_path")
        stale = None
        if src:
            sp = Path(src)
            stale = "missing" if not sp.is_file() else (sha256_of(sp) != meta.get("source_sha256"))
        pages.append({
            "rel": rel.as_posix(),
            "category": rel.parent.as_posix().strip("."),
            "title": meta.get("title") or p.stem,
            "mode": meta.get("mode"),
            "mode_label": meta.get("mode_label"),
            "model": meta.get("model"),
            "reasoning_effort": meta.get("reasoning_effort", ""),
            "generated_at": meta.get("generated_at"),
            "source_path": src,
            "source_root": meta.get("source_root"),
            "source_rel": meta.get("source_rel"),
            "warnings": meta.get("warnings", []),
            "stale": stale,
            "size": p.stat().st_size,
            "history": history_count(out, rel.as_posix()),
            "figures": meta.get("figures") or {},
        })
    groups: dict[tuple, list[dict]] = {}
    for page in pages:
        if page["source_path"]:
            key = (page["category"], Path(page["source_path"]).resolve())
            groups.setdefault(key, []).append({k: page[k] for k in (
                "rel", "mode", "mode_label", "model", "reasoning_effort", "generated_at")})
    for page in pages:
        key = (page["category"], Path(page["source_path"]).resolve()) if page["source_path"] else None
        page["versions"] = groups.get(key, [])
    return pages


def create_category(out: Path, rel: str) -> str:
    r = clean_rel(rel, allow_empty=False)
    join_inside(out, r).mkdir(parents=True, exist_ok=True)
    return r.as_posix()


def rename_category(out: Path, rel: str, new_rel: str) -> str:
    src = join_inside(out, clean_rel(rel, allow_empty=False))
    dst_rel = clean_rel(new_rel, allow_empty=False)
    dst = join_inside(out, dst_rel)
    if not src.is_dir():
        raise PathError("카테고리가 없습니다.")
    if dst.exists():
        raise PathError("같은 이름의 카테고리가 이미 있습니다.")
    if src in dst.parents:
        raise PathError("카테고리를 자기 하위로 옮길 수 없습니다.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    hsrc = join_inside(out, PurePosixPath(HISTORY) / clean_rel(rel))
    if hsrc.is_dir():
        hdst = join_inside(out, PurePosixPath(HISTORY) / dst_rel)
        hdst.parent.mkdir(parents=True, exist_ok=True)
        hsrc.rename(hdst)
    return dst_rel.as_posix()


def delete_category(out: Path, rel: str, *, force: bool = False) -> None:
    d = join_inside(out, clean_rel(rel, allow_empty=False))
    if not d.is_dir():
        raise PathError("카테고리가 없습니다.")
    if any(d.rglob("*.html")) and not force:
        raise PathError("페이지가 들어 있는 카테고리입니다. 함께 삭제하려면 force를 지정하세요.")
    shutil.rmtree(d)
    h = join_inside(out, PurePosixPath(HISTORY) / clean_rel(rel))
    if h.is_dir():
        shutil.rmtree(h)


def page_path(out: Path, rel: str) -> Path:
    p = join_inside(out, clean_rel(rel, allow_empty=False))
    if p.suffix.lower() != ".html":
        raise PathError("HTML 페이지 경로가 아닙니다.")
    return p


def delete_page(out: Path, rel: str) -> None:
    p = page_path(out, rel)
    if not p.is_file():
        raise PathError("페이지가 없습니다.")
    p.unlink()
    delete_history(out, rel)


def move_page(out: Path, rel: str, category: str) -> str:
    src = page_path(out, rel)
    if not src.is_file():
        raise PathError("페이지가 없습니다.")
    cat = clean_rel(category)
    dst = join_inside(out, cat / src.name)
    if dst.exists():
        raise PathError("대상 카테고리에 같은 이름의 페이지가 있습니다.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    hsrc = history_dir(out, rel)
    src.rename(dst)
    new_rel = dst.relative_to(out.resolve()).as_posix()
    if hsrc.is_dir():
        hdst = history_dir(out, new_rel)
        if hdst.exists():
            shutil.rmtree(hdst)
        hdst.parent.mkdir(parents=True, exist_ok=True)
        hsrc.rename(hdst)
    return new_rel


# --------------------------------------------------------------------------- page history
#
# Overwritten pages are kept as <out>/.history/<category>/<stem>/<YYYYmmdd-HHMMSS>.html.
# Dot folders are hidden from the library listing but still served by /view/.

HISTORY = ".history"
_VERSION_RE = re.compile(r"^\d{8}-\d{6}(-\d+)?$")


def history_dir(out: Path, rel: str | PurePosixPath) -> Path:
    r = clean_rel(str(rel), allow_empty=False)
    return join_inside(out, PurePosixPath(HISTORY) / r.parent / r.stem)


def archive_page(out: Path, rel: str | PurePosixPath, keep: int = 0) -> str | None:
    """Move the current page at rel into its history folder. Returns the version name."""
    src = page_path(out, str(rel))
    if not src.is_file():
        return None
    d = history_dir(out, rel)
    d.mkdir(parents=True, exist_ok=True)
    base = datetime.now().strftime("%Y%m%d-%H%M%S")
    name, n = base, 0
    while (d / f"{name}.html").exists():
        n += 1
        name = f"{base}-{n}"
    src.replace(d / f"{name}.html")
    if keep > 0:
        for old in sorted(d.glob("*.html"))[:-keep]:
            old.unlink(missing_ok=True)
    return name


def list_history(out: Path, rel: str) -> list[dict]:
    d = history_dir(out, rel)
    if not d.is_dir():
        return []
    items = []
    for p in sorted(d.glob("*.html"), reverse=True):
        meta = read_meta(p)
        items.append({
            "version": p.stem,
            "rel": p.relative_to(out.resolve()).as_posix(),
            "generated_at": meta.get("generated_at"),
            "mode": meta.get("mode"),
            "mode_label": meta.get("mode_label"),
            "model": meta.get("model"),
            "warnings": meta.get("warnings", []),
            "figures": meta.get("figures", {}),
            "size": p.stat().st_size,
        })
    return items


def _version_path(out: Path, rel: str, version: str) -> Path:
    if not _VERSION_RE.match(version or ""):
        raise PathError("버전 이름이 잘못되었습니다.")
    p = history_dir(out, rel) / f"{version}.html"
    if not p.is_file():
        raise PathError("해당 이전 버전이 없습니다.")
    return p


def restore_history(out: Path, rel: str, version: str, keep: int = 0) -> str | None:
    """Make an old version current again; the current page goes into history first."""
    old = _version_path(out, rel, version)
    target = page_path(out, rel)
    staged = old.with_name(old.name + ".restoring")
    old.replace(staged)
    archived = archive_page(out, rel, keep=0)
    target.parent.mkdir(parents=True, exist_ok=True)
    staged.replace(target)
    if keep > 0:
        d = history_dir(out, rel)
        for extra in sorted(d.glob("*.html"))[:-keep]:
            extra.unlink(missing_ok=True)
    return archived


def delete_history(out: Path, rel: str, version: str | None = None) -> None:
    if version:
        _version_path(out, rel, version).unlink()
        return
    d = history_dir(out, rel)
    if d.is_dir():
        shutil.rmtree(d)


def history_count(out: Path, rel: str) -> int:
    d = history_dir(out, rel)
    return len(list(d.glob("*.html"))) if d.is_dir() else 0
