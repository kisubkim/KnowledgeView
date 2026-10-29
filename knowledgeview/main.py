"""FastAPI app: management API, the management UI at /, generated pages at /view/..., libraries at /assets/..."""

from __future__ import annotations

import secrets
import hashlib
import socket
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, claude_check, codex_check, export, library
from .config import (OPENAI_PREFIX, PACKAGE_DIR, Settings, admin_token, data_dir, install_config_path, mode_ids,
                     mode_label, port_overridden, save_install_config, server_address)
from .generate import generate
from .cache import Cache
from .jobs import Job, JobManager
from .library import PathError, clean_rel, join_inside
from .llm import LLMError, make_client

STATIC = PACKAGE_DIR / "static"
NO_CACHE = {"Cache-Control": "no-cache"}

settings = Settings()
app = FastAPI(title="KnowledgeView", version=__version__)
app.mount("/assets", StaticFiles(directory=STATIC), name="assets")
# Set by the desktop app (tray): an object with quit() and hide(). None when run as a plain server.
app.state.desktop = None
app.state.port = None  # the port actually being served (set by the desktop app)


def _run(job: Job) -> dict:
    def progress(done: int, total: int, msg: str) -> None:
        job.done, job.total, job.message = done, total, msg
        job.add_log(msg)

    return generate(
        source_path=job.source_path, source_root=job.source_root, category=job.category, mode=job.mode,
        settings=settings.get(), output_dir=settings.output_dir(), progress=progress, cancel=job.cancel,
        failures_dir=data_dir() / "llm_failures",
        cache=Cache(data_dir() / "cache", enabled=not job.refresh),
        output_rel=job.output_rel,
    )


jobs = JobManager(_run)


@app.exception_handler(PathError)
async def _path_error(request: Request, exc: PathError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


def require_admin(request: Request) -> None:
    token = admin_token()
    if not token:
        return
    given = request.headers.get("x-kv-token") or request.cookies.get("kv_token") or ""
    if not secrets.compare_digest(given, token):
        raise HTTPException(401, "관리 토큰이 필요합니다.")


Admin = Depends(require_admin)


# --------------------------------------------------------------------------- pages


@app.get("/", include_in_schema=False)
def index():
    # Content-based URLs prevent browsers from reusing an older management UI.
    text = (STATIC / "index.html").read_text(encoding="utf-8")
    for name in ("app.js", "app.css"):
        version = hashlib.sha256((STATIC / name).read_bytes()).hexdigest()[:16]
        text = text.replace(f'/assets/{name}"', f'/assets/{name}?v={version}"')
    return HTMLResponse(text, headers={"Cache-Control": "no-store"})


@app.get("/view/{rel:path}", include_in_schema=False)
def view(rel: str):
    out = settings.output_dir()
    path = join_inside(out, clean_rel(rel, allow_empty=True))
    if path.is_dir():
        return RedirectResponse("/#/library/" + rel.strip("/"))
    if path.suffix.lower() != ".html" or not path.is_file():
        raise HTTPException(404, "페이지가 없습니다.")
    text = path.read_text(encoding="utf-8", errors="replace")
    if "kv-page.js" not in text:
        # Pages made before the shared runtime existed still get its fixes (diagram contrast).
        idx = text.rfind("</body>")
        tag = '<script src="/assets/kv-page.js" defer></script>\n'
        text = text[:idx] + tag + text[idx:] if idx >= 0 else text + tag
    return HTMLResponse(text, headers=NO_CACHE)


# --------------------------------------------------------------------------- status & settings


def _is_local(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1", "localhost")


def _claude_status(force: bool = False) -> dict:
    return claude_check.status(settings.get()["claude"]["command"] or "claude", force=force)


def _codex_status(force: bool = False) -> dict:
    return codex_check.status(settings.get()["codex"]["command"] or "codex", force=force)


@app.get("/api/status")
def status(request: Request):
    s = settings.get()
    claude = _claude_status()
    codex = _codex_status()
    codex_modes = [{"id": "codex_cli", "label": mode_label("codex_cli"), "available": True,
                    "note": codex["path"]}] if codex_check.usable(codex) else []
    # The Claude Code mode is offered only when the CLI is installed and logged in.
    claude_modes = [{"id": "claude_cli", "label": mode_label("claude_cli"), "available": True,
                     "note": claude["path"] + (f" · {claude['subscription']}" if claude.get("subscription") else "")}]         if claude_check.usable(claude) else []
    return {
        "version": __version__,
        "auth_required": bool(admin_token()),
        "desktop": app.state.desktop is not None,
        "local": _is_local(request),
        "exit_action": s.get("exit_action", "ask"),
        "claude": {k: claude.get(k) for k in ("installed", "logged_in", "auth_method", "subscription", "note")},
        "codex": {k: codex.get(k) for k in ("installed", "logged_in", "note")},
        "modes": [
            {"id": "none", "label": mode_label("none"), "available": True, "note": ""},
        ] + claude_modes + codex_modes + [
            {"id": OPENAI_PREFIX + ep["id"], "label": mode_label(OPENAI_PREFIX + ep["id"], s),
             "available": bool(ep["base_url"] and ep["model"]),
             "note": f"{ep['base_url']} · {ep['model']}" if ep["base_url"] and ep["model"] else "Base URL 또는 모델 미설정"}
            for ep in s["openai_endpoints"]
        ],
        "default_mode": s["default_mode"],
    }


@app.post("/api/claude/check", dependencies=[Admin])
def claude_recheck():
    st = _claude_status(force=True)
    return {k: st.get(k) for k in ("installed", "logged_in", "auth_method", "subscription", "note")}


@app.post("/api/codex/check", dependencies=[Admin])
def codex_recheck():
    st = _codex_status(force=True)
    return {k: st.get(k) for k in ("installed", "logged_in", "note")}


@app.get("/api/codex/models", dependencies=[Admin])
def codex_models():
    return {"models": codex_check.models()}


class ExitBody(BaseModel):
    action: str  # "quit" | "tray"
    remember: bool = False


@app.post("/api/app/exit", dependencies=[Admin])
def app_exit(body: ExitBody, request: Request):
    """Desktop app only, and only from this PC: quit everything or keep running in the tray."""
    if app.state.desktop is None:
        raise HTTPException(400, "트레이 앱으로 실행 중이 아닙니다.")
    if not _is_local(request):
        raise HTTPException(403, "종료는 서버 PC에서만 할 수 있습니다.")
    if body.action not in ("quit", "tray"):
        raise HTTPException(400, "action은 quit 또는 tray입니다.")
    if body.remember:
        settings.update({"exit_action": body.action})
    if body.action == "quit":
        for j in jobs.list():
            if j["status"] in ("queued", "running"):
                jobs.cancel(j["id"])
        app.state.desktop.quit(delay=0.5)
    return {"ok": True, "action": body.action}


def _running_port(request: Request) -> int:
    return app.state.port or request.url.port or server_address()[1]


def _port_free(port: int) -> bool:
    # On Windows a 0.0.0.0 bind can succeed while another program listens on 127.0.0.1,
    # so first see whether anything answers there, then try the bind the server will do.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE if hasattr(socket, "SO_EXCLUSIVEADDRUSE") else socket.SO_REUSEADDR, 1)
        try:
            s.bind((server_address()[0], port))
            return True
        except OSError:
            return False


@app.get("/api/server", dependencies=[Admin])
def get_server(request: Request):
    host, port = server_address()
    return {
        "configured_port": port,
        "running_port": _running_port(request),
        "host": host,
        "env_override": port_overridden(),
        "config_path": str(install_config_path()),
        "desktop": app.state.desktop is not None,
        "local": _is_local(request),
    }


class ServerBody(BaseModel):
    port: int


@app.put("/api/server", dependencies=[Admin])
def put_server(body: ServerBody, request: Request):
    """Change the port (takes effect on the next start). Only from this PC."""
    if not _is_local(request):
        raise HTTPException(403, "포트는 서버 PC에서만 바꿀 수 있습니다.")
    if not 1024 <= body.port <= 65535:
        raise HTTPException(400, "포트는 1024~65535 사이여야 합니다.")
    running = _running_port(request)
    if body.port != running and not _port_free(body.port):
        raise HTTPException(400, f"포트 {body.port}는 다른 프로그램이 쓰고 있습니다. 다른 번호를 고르세요.")
    path = save_install_config({"port": body.port})
    return {"ok": True, "port": body.port, "running_port": running, "restart_required": body.port != running,
            "config_path": str(path), "env_override": port_overridden()}


@app.post("/api/app/restart", dependencies=[Admin])
def app_restart(request: Request):
    if app.state.desktop is None:
        raise HTTPException(400, "트레이 앱으로 실행 중이 아닙니다. 직접 다시 실행하세요.")
    if not _is_local(request):
        raise HTTPException(403, "다시 시작은 서버 PC에서만 할 수 있습니다.")
    for j in jobs.list():
        if j["status"] in ("queued", "running"):
            jobs.cancel(j["id"])
    app.state.desktop.restart(delay=0.5)
    return {"ok": True, "port": server_address()[1]}


@app.post("/api/auth/check", dependencies=[Admin])
def auth_check():
    return {"ok": True}


@app.get("/api/settings", dependencies=[Admin])
def get_settings():
    return settings.public()


@app.put("/api/settings", dependencies=[Admin])
def put_settings(patch: dict):
    patch.pop("output_dir_resolved", None)
    try:
        settings.update(patch)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return settings.public()


class LlmTest(BaseModel):
    mode: str


@app.post("/api/llm/test", dependencies=[Admin])
def llm_test(body: LlmTest):
    try:
        client = make_client(body.mode, settings.get())
        out = client.complete("짧게 답한다.", "연결 확인입니다. '정상'이라고만 답하세요.")
    except LLMError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "reply": out.strip()[:200], "model": client.model}


# --------------------------------------------------------------------------- library


@app.get("/api/library")
def get_library():
    out = settings.output_dir()
    return {"output_dir": str(out), "categories": library.list_categories(out), "pages": library.list_pages(out)}


class CategoryBody(BaseModel):
    path: str
    new_path: str | None = None


@app.post("/api/categories", dependencies=[Admin])
def create_category(body: CategoryBody):
    return {"path": library.create_category(settings.output_dir(), body.path)}


@app.patch("/api/categories", dependencies=[Admin])
def rename_category(body: CategoryBody):
    if not body.new_path:
        raise HTTPException(400, "새 이름이 필요합니다.")
    return {"path": library.rename_category(settings.output_dir(), body.path, body.new_path)}


@app.delete("/api/categories", dependencies=[Admin])
def delete_category(path: str, force: bool = False):
    library.delete_category(settings.output_dir(), path, force=force)
    return {"ok": True}


@app.delete("/api/pages", dependencies=[Admin])
def delete_page(rel: str):
    library.delete_page(settings.output_dir(), rel)
    return {"ok": True}


class MoveBody(BaseModel):
    rel: str
    category: str


@app.post("/api/pages/move", dependencies=[Admin])
def move_page(body: MoveBody):
    return {"rel": library.move_page(settings.output_dir(), body.rel, body.category)}


class RegenBody(BaseModel):
    rels: list[str]
    mode: str | None = None
    refresh: bool = False


@app.post("/api/pages/regenerate", dependencies=[Admin])
def regenerate(body: RegenBody):
    out = settings.output_dir()
    created = []
    for rel in body.rels:
        page = library.page_path(out, rel)
        meta = library.read_meta(page)
        src, root = meta.get("source_path"), meta.get("source_root")
        if not src or not Path(src).is_file():
            raise HTTPException(400, f"원본 MD를 찾을 수 없습니다: {rel}")
        mode = _check_mode(body.mode or meta.get("mode") or settings.get()["default_mode"])
        category = Path(rel).parent.as_posix().strip(".")
        previous_mode = meta.get("mode")
        if previous_mode == "openai":
            eps = settings.get()["openai_endpoints"]
            previous_mode = OPENAI_PREFIX + eps[0]["id"] if eps else previous_mode
        created.append(_submit(Path(src), Path(root or Path(src).parent), meta.get("source_rel") or Path(src).name, category, mode,
                               body.refresh, output_rel=rel if mode == previous_mode else None))
    return {"jobs": created}


@app.get("/api/pages/figures.zip")
def export_figures(rel: str):
    """All figures of a page (and its mermaid sources) as a ZIP. Read-only, like /view."""
    page = library.page_path(settings.output_dir(), rel)
    if not page.is_file():
        raise HTTPException(404, "페이지가 없습니다.")
    data, _count = export.build_zip(page)
    name = f"{page.stem}_그림.zip"
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": f"attachment; filename=\"figures.zip\"; filename*=UTF-8''{quote(name)}",
        **NO_CACHE,
    })


@app.get("/api/pages/history")
def page_history(rel: str):
    return {"rel": rel, "versions": library.list_history(settings.output_dir(), rel)}


class HistoryBody(BaseModel):
    rel: str
    version: str


@app.post("/api/pages/history/restore", dependencies=[Admin])
def restore_page(body: HistoryBody):
    archived = library.restore_history(settings.output_dir(), body.rel, body.version,
                                       keep=int(settings.get().get("history_keep") or 0))
    return {"ok": True, "archived": archived}


@app.delete("/api/pages/history", dependencies=[Admin])
def delete_page_history(rel: str, version: str | None = None):
    library.delete_history(settings.output_dir(), rel, version)
    return {"ok": True}


# --------------------------------------------------------------------------- sources & generation


@app.get("/api/sources", dependencies=[Admin])
def get_sources():
    roots = settings.source_roots()
    by_source: dict[str, list[str]] = {}
    source_modes: dict[str, list[str]] = {}
    for p in library.list_pages(settings.output_dir()):
        if p["source_path"]:
            by_source.setdefault(str(Path(p["source_path"])), []).append(p["rel"])
            source_modes.setdefault(str(Path(p["source_path"])), []).append(p["mode"])
    data = library.list_sources(roots)
    for entry in data:
        entry["is_uploads"] = entry["index"] == len(roots) - 1
        for f in entry["files"]:
            f["pages"] = by_source.get(f["path"], [])
            f["page_modes"] = source_modes.get(f["path"], [])
            f["default_category"] = library.default_category(f["rel"])
    return {"roots": data}


class GenItem(BaseModel):
    root: int
    rel: str
    category: str | None = None


class GenBody(BaseModel):
    items: list[GenItem]
    mode: str | None = None
    refresh: bool = False


def _check_mode(mode: str) -> str:
    if mode == "codex_cli" and not codex_check.usable(_codex_status()):
        raise HTTPException(400, "Codex CLI가 설치되어 있지 않거나 로그인되어 있지 않습니다. 다른 방식을 고르세요.")
    if mode == "claude_cli" and not claude_check.usable(_claude_status()):
        raise HTTPException(400, "Claude Code가 설치되어 있지 않거나 로그인되어 있지 않습니다. 다른 방식을 고르세요.")
    if mode == "openai":  # pages made before endpoints became a list
        eps = settings.get()["openai_endpoints"]
        mode = OPENAI_PREFIX + eps[0]["id"] if eps else mode
    if mode not in mode_ids(settings.get()):
        if mode.startswith(OPENAI_PREFIX) or mode == "openai":
            raise HTTPException(400, f"OpenAI 호환 엔드포인트가 없습니다(삭제되었을 수 있음): {mode}. 다른 방식을 고르세요.")
        raise HTTPException(400, f"알 수 없는 모드: {mode}")
    return mode


def _submit(src: Path, root: Path, label: str, category: str, mode: str, refresh: bool = False,
            output_rel: str | None = None) -> dict:
    category = clean_rel(category).as_posix().strip(".")
    job = Job(source_path=src, source_root=root, source_label=label, category=category, mode=mode,
              refresh=refresh, output_rel=output_rel)
    return jobs.submit(job).to_dict()


@app.post("/api/generate", dependencies=[Admin])
def post_generate(body: GenBody):
    roots = settings.source_roots()
    mode = _check_mode(body.mode or settings.get()["default_mode"])
    created = []
    for it in body.items:
        src = library.resolve_source(roots, it.root, it.rel)
        category = it.category if it.category is not None else library.default_category(it.rel)
        created.append(_submit(src, roots[it.root], it.rel, category, mode, body.refresh))
    return {"jobs": created}


@app.post("/api/upload", dependencies=[Admin])
async def upload(files: list[UploadFile] = File(...), category: str = Form(""), mode: str = Form(""),
                 generate_now: bool = Form(True), refresh: bool = Form(False)):
    cat = clean_rel(category)
    up_root = settings.uploads_dir()
    target_dir = join_inside(up_root, cat)
    target_dir.mkdir(parents=True, exist_ok=True)
    saved, created = [], []
    for f in files:
        name = Path(f.filename or "").name
        if not name.lower().endswith(".md"):
            raise HTTPException(400, f"MD 파일만 올릴 수 있습니다: {name}")
        dest = join_inside(target_dir, clean_rel(name, allow_empty=False))
        dest.write_bytes(await f.read())
        rel = dest.relative_to(up_root.resolve()).as_posix()
        saved.append(rel)
        if generate_now:
            m = _check_mode(mode or settings.get()["default_mode"])
            created.append(_submit(dest, up_root, rel, cat.as_posix().strip("."), m, refresh))
    return {"saved": saved, "jobs": created}


@app.get("/api/jobs")
def list_jobs():
    return {"jobs": jobs.list()}


@app.post("/api/jobs/{job_id}/cancel", dependencies=[Admin])
def cancel_job(job_id: str):
    if not jobs.cancel(job_id):
        raise HTTPException(404, "취소할 수 있는 작업이 없습니다.")
    return {"ok": True}


@app.delete("/api/jobs", dependencies=[Admin])
def clear_jobs():
    jobs.clear_finished()
    return {"ok": True}
