# Repository Guidelines

## Project Structure & Module Organization

KnowledgeView converts Markdown into enriched HTML and serves it through FastAPI. Backend modules live in `knowledgeview/`: `main.py` defines routes, `generate.py` coordinates generation, `render.py` renders pages, and `library.py` manages files. The vanilla JavaScript UI and shared page runtime live in `knowledgeview/static/`; Jinja2 templates live in `knowledgeview/templates/`. Windows tray and installer code is in `knowledgeview/desktop/`. Tests are in `tests/test_core.py`. Runtime `data/`, generated `output/`, and packaging directories `build/` and `dist/` are gitignored.

## Build, Test, and Development Commands

Run these from the repository root in PowerShell:

- `.\setup.bat`: create `.venv` and install development dependencies; targets Python 3.12.
- `.\start.bat`: start the web app on port 8770 by default, binding to `0.0.0.0`.
- `.\.venv\Scripts\python -m knowledgeview --host 127.0.0.1 --port 8770`: run locally with explicit binding.
- `.\.venv\Scripts\python -m pytest -q`: run the test suite.
- `.\.venv\Scripts\python -m pip install -r requirements-desktop.txt`: install desktop and packaging dependencies.
- `powershell -ExecutionPolicy Bypass -File build.ps1`: install desktop dependencies, run tests, and package `dist/KnowledgeView-Setup.exe`.

The frontend requires no build step.

## Coding Style & Naming Conventions

Follow existing code: four-space Python indentation, type hints where practical, `snake_case` functions/modules, and `PascalCase` classes. JavaScript uses two-space indentation and `camelCase` functions. Keep UI text and ordinary documentation in Korean, and code comments in English. No formatter or linter is configured.

## Testing Guidelines

Use pytest with `test_*.py` files and descriptive `test_*` functions. Use `tmp_path` for filesystem fixtures and fake LLM clients for deterministic tests. Cover changed behavior and regressions; no numeric coverage threshold is configured. Manually verify UI and desktop changes, since pytest provides limited desktop coverage. Test packaged executables with `--no-install` to avoid modifying the development machine.

## Commit & Pull Request Guidelines

History uses concise Korean summaries describing the change; no mandatory prefix convention is evident. Keep commits focused. PRs should explain the problem, resulting behavior, and validation commands; link related issues and include screenshots for visual changes.

## Data & Configuration Safety

Never write into source Markdown folders. Preserve page history when replacing output and keep embedded `kv-meta` metadata stable. Validate user paths with `library.clean_rel` and `join_inside`. Keep credentials and local settings out of Git; set `KV_ADMIN_TOKEN` before exposing management features to other PCs. Bundle browser libraries locally and record their licenses in `THIRD_PARTY_NOTICES.md`.
