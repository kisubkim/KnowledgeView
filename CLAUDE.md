# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

KnowledgeView is a local web app (FastAPI + vanilla JS, no build step). It turns Markdown documents into detailed HTML pages and serves them for viewing. The user's requirements:
- **One generated page = one `.html` file**, with its CSS/JS and images (data URIs) inside. Rendering code is *not* inlined: the page loads mermaid (`/assets/vendor/`) and the shared runtime `/assets/kv-page.js` from the app, so pages are meant to be viewed through the app and runtime fixes reach existing pages without regeneration (the user chose this). Pages must also work when reached from other PCs over the web.
- **Source MD folders are read-only.** Never write into them. Output goes to `output/` (configurable), one folder per category. By default the category mirrors the source folder (`01_LLM_기초_이론/x.md` → `output/01_LLM_기초_이론/x.html`). Nested categories are nested folders.
- Enrichment modes the user can pick per job: `none` (template restructuring only), `claude_cli` (`claude -p` on this PC), and one `openai:<endpoint id>` mode per configured OpenAI-compatible endpoint (`settings.openai_endpoints`, added/removed in the 설정 tab). Mode helpers live in `config.py` (`mode_ids`, `endpoint_for`, `mode_label`); a bare `openai` (pages and settings from 0.1) means the first endpoint, and `Settings._load` migrates the old single `openai` block.
- **Overwriting a page archives the old one** (user requirement): `generate()` writes the new page to a temp file, then `library.archive_page` moves the current file to `<out>/.history/<category>/<stem>/<YYYYmmdd-HHMMSS>.html` (pruned to `history_keep`), then swaps in the new file. A failed or cancelled job leaves the page untouched. Moving a page or renaming a category moves its history; deleting a page or category deletes it. Dot folders are hidden from the library but `/view/.history/...` serves them.

Sample sources: `D:\AI_Docs\docs_base` (the user's notes on LLMs; mermaid-heavy, Korean). Source roots are a setting (`data/settings.json`), so check them before assuming a path.

## Desktop build (`knowledgeview/desktop/`)

`build.ps1` makes `dist\KnowledgeView-Setup.exe` (PyInstaller onefile, windowed; entry `KnowledgeView.pyw` → `desktop/entry.py`). The design follows the TokenChecker project in this workspace (self-installing exe, `single_instance.py` mutex + SHOW/QUIT events, pystray tray). Flags: `--install` (install window again), `--uninstall`, `--tray` (start without opening the browser; used by the sign-in Run key), `--no-install` (run a frozen build in place). Dev run: `.venv/Scripts/python -m knowledgeview --desktop [--tray]` (needs `requirements-desktop.txt`).
- When frozen, `config.APP_ROOT` is the exe's folder and `install_config()` reads `knowledgeview.json` there (written by `installer.install`: `data_dir`, `host`, `port`, `admin_token`). The installer also seeds `<data>/settings.json` with `output_dir` and the optional source folder. Env vars (`KV_DATA_DIR`, `KV_PORT`, ...) still override, which is how a frozen build is tested without installing: `KV_PORT=8772 KV_DATA_DIR=<tmp> dist/KnowledgeView-Setup.exe --no-install --tray`.
- `desktop/app.py` runs uvicorn in a thread (`log_config=None`; stdout/stderr go to `<data>/logs/console.log` because a windowed exe has none) and the tray on the main thread; it sets `main.app.state.desktop`, which enables `POST /api/app/exit` (`quit` | `tray`, local requests only) and the 종료 button. `settings.exit_action` (`ask`/`quit`/`tray`) remembers the choice.
- `claude_check.py` runs `claude auth status` (cached 60 s; only installed/logged_in/auth method/subscription are exposed). `/api/status` lists the `claude_cli` mode only when `usable()`; `_check_mode` rejects it otherwise. `POST /api/claude/check` refreshes.
- Never run an actual install/uninstall casually on the dev machine (registry, Start Menu, Programs folder); test with `--no-install`. To look at the install window, capture it with `PrintWindow` (a screen copy can include the user's other windows).
- Port: `config.server_address()` = `KV_HOST`/`KV_PORT` env, else `knowledgeview.json` (`save_install_config`; in a source checkout it is created at the repo root and gitignored), else 127.0.0.1:8770. 설정 > 서버 uses `GET/PUT /api/server` (local requests only; `_port_free` both connects to 127.0.0.1 and binds, because on Windows a 0.0.0.0 bind succeeds while another program listens on 127.0.0.1) and `POST /api/app/restart` → `Desktop.restart()`, which starts a new copy with `--restarted --tray` (it waits up to 30 s for the old copy's mutex) and quits; the old tab polls the new port and moves there.
- **Starting another copy of the frozen exe from inside it** (restart, installer launching the installed exe) must pass `env=fresh_env()` (`PYINSTALLER_RESET_ENVIRONMENT=1`). Without it the child reuses the parent's `_MEI` temp folder, which the parent deletes on exit → `No module named 'unicodedata'` and a "Failed to remove temporary directory" warning.
- `tests/test_core.py::test_all_modules_import` imports every module; the desktop/installer code is otherwise untested by pytest, and a syntax error there only shows up as a PyInstaller "Unhandled exception" dialog.
- PyInstaller needs `--collect-submodules` for uvicorn and pygments (lexers/styles are imported by name) plus `--add-data` for `static/` and `templates/`.

## Commands

```bash
setup.bat                                        # first time: .venv (Python 3.12) + requirements-dev.txt
start.bat                                        # serve on 0.0.0.0:8770 (KV_HOST/KV_PORT)
.venv/Scripts/python -m knowledgeview --port 8770   # dev run (default host 127.0.0.1)
.venv/Scripts/python -m pytest -q                # all tests (~1 s, no LLM needed)
.venv/Scripts/python -m pytest -q tests/test_core.py -k normalize   # single test
```

Env vars: `KV_ADMIN_TOKEN` (when set, every mutating/management endpoint needs header `X-KV-Token` or cookie `kv_token`; viewing and `/api/library` stay open), `KV_DATA_DIR` (default `data/`: `settings.json`, `uploads/`, `llm_failures/`). Everything else is in `data/settings.json`, edited from the 설정 tab (`config.py` `DEFAULTS` is the schema; unknown keys are dropped on merge).

Endpoint settings: an empty `api_key` in a PUT keeps that endpoint's stored key (matched by `id`), `clear_api_key` removes it; endpoints without `id` get one. `reasoning_effort` is sent only when set; thinking models (Ollama gemma4) otherwise spend `max_tokens` on reasoning and every call ends with `finish_reason: length`. Cancelling a job does not abort an in-flight OpenAI request (the server keeps generating until it returns).

To check the UI, screenshot with headless Edge: `"/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" --headless=new --window-size=1400,2200 --virtual-time-budget=8000 --screenshot=<png> <url>` (percent-encode Korean in URLs from Git Bash; curl from Git Bash sends them in the wrong encoding, so use Python/httpx for API checks).

## Architecture (`knowledgeview/`)

Pipeline for one file, `generate.generate()`:
1. `mdparse.parse()` splits the MD into title (first `#`), header metadata (a leading `> **키**: 값` blockquote, or simple front matter), intro, and **level-2 sections**. Splitting tracks code fences (`iter_unfenced`), because `# comment` lines in bash blocks are not headings.
2. LLM modes enrich **each `##` section separately** (thread pool, `concurrency` per backend), plus one whole-document call for the overview JSON (summary, objectives, key_points, prerequisites, glossary, quiz). Sections matching `skip_headings` (참고 문헌...) are never sent. Prompts are in `prompts.py`. The contract: keep the original text untouched and add content **only** as callouts `> [!DETAIL|EXAMPLE|ANALOGY|PITFALL|SUMMARY]`.
   With `images.enabled`, the section prompt also allows up to `max_per_section` placeholder lines `[[FIGURE | caption: … | search: … | draw: …]]` (see `figures.py`). After the sections, each placeholder is resolved: Wikimedia Commons search (`query_variants` shortens the query, because Commons ANDs all words; only CC0/PD/CC BY/CC BY-SA; the LLM picks a candidate from titles/descriptions or none), else an LLM-drawn SVG (`sanitize_svg`), else the line is dropped. Figures become one-line `<figure class="kv-figure kv-added">` HTML with a data-URI `<img>` and a credit caption. HTTPS goes through `figures.http_client()` (truststore: this PC's antivirus intercepts TLS, so certifi alone fails).
   LLM results are cached in `data/cache/{sections,overview,figures}` (`cache.py`); keys hash mode, model, prompt text and input, so editing a prompt invalidates its entries. Jobs with `refresh` skip cache reads but still write.
3. `normalize_section` strips a preamble/restores the heading; `validate_section` rejects output that lost mermaid blocks or headings or shrank below 90% of the original. A rejected section is retried once (`SECTION_RETRIES`), the raw answer is saved to `data/llm_failures/`, and the page shows the original section with a warning. If every section fails, the job fails.
4. `render.build_page()` renders Markdown with markdown-it-py (with `StateInline.scanDelims` patched so `**강조(x)**이라고`-style Korean emphasis closes) (one `MdRenderer` per page so heading ids stay unique) and the Jinja2 template `templates/page.html.j2`. The renderer turns mermaid fences into `<pre class="mermaid">` (plus a hidden source copy used for re-rendering on theme change), highlights code with Pygments at build time, maps callouts to styled boxes (the added types get `kv-added`, which the page's "보강 내용" toggle hides), inlines local images as data URIs, and rewrites links to other `.md` files in the same source root to their **mirrored** output path.
5. The page embeds its metadata as `<script type="application/json" id="kv-meta">` in `<head>` (source path/root/rel, sha256, mode, model, category, warnings). **The library has no database**: `library.list_pages()` reads this block from each HTML (first 64 KB) and compares the sha256 to flag "원본 변경됨". Keep the block in `<head>` and keep its keys stable.

Other modules:
- `library.py`: path safety (`clean_rel`, `join_inside` — use them for every user-supplied path), source listing, category/page CRUD on plain folders.
- `jobs.py`: in-memory queue, **one worker thread** (jobs run one at a time; parallelism is inside a job). Cancel sets an event; `ClaudeCliClient` kills its subprocess on cancel. Jobs are lost on restart.
- `llm.py`: `ClaudeCliClient` runs `claude -p --tools "" --no-session-persistence --output-format text --system-prompt ...` with the prompt on stdin, from an empty temp dir (so no project CLAUDE.md leaks in). `OpenAIClient` posts to `{base_url}/chat/completions` and treats `finish_reason == "length"` as an error. `<think>` blocks are stripped in `generate.py`.
- `static/kv-page.js`: the page runtime. Pages with `<meta name="kv-runtime">` let it render mermaid (re-render on `data-theme` change, zoom/source tools, figure lightbox). Pages made before it existed render inline; `/view` injects the script into them and it only fixes contrast. The contrast fix finds each label's background geometrically (HTML label background, else filled shapes under the label centre composited in paint order, else the diagram background) and recolours text below WCAG 4.5:1. It writes `data-kv-contrast="checked/fixed/still-low"` on `<html>`, which headless `--dump-dom` checks can read.
- The runtime also owns the **viewer** (`openViewer`): a full-screen overlay for diagrams (`.kv-zoom` button or click on the SVG) and figures (click). Zoom changes the cloned SVG's width/height (not a CSS scale) so text stays sharp; wheel/drag/pinch/double-click/keys; SVG/PNG save (PNG via canvas; mermaid's foreignObject labels did not taint the canvas in Edge). It adds a "그림 내보내기" link to the page top bar under `/view/`. `export.build_zip` (`GET /api/pages/figures.zip?rel=`, read-only like `/view`) extracts the data-URI figures, credits and mermaid sources from a page's HTML.
- `main.py`: API under `/api/*`, UI at `/` (`static/index.html`, `app.js`, `app.css`; hash routes `#/library[/<category>]`, `#/generate`, `#/jobs`, `#/settings`), pages at `/view/<category>/<file>.html`, static at `/assets/`. Uploaded MD files go to `data/uploads/<category>/` and that folder is always the last source root.

## Conventions

- UI strings, docs and commit messages are Korean; code comments are English.
- Generated pages must stay single files: put new page CSS/JS in `page.html.j2`; put new heavy libraries under `static/vendor/` (record version and license in README) and load them from `asset_base`, never from a CDN.
- `mermaid.min.js` is pinned (11.17.2, MIT). After a template change, existing pages only change after 재생성.
- `data/` and `output/` are gitignored (settings may hold an API key).
