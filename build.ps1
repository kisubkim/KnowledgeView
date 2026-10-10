# Builds dist\KnowledgeView-Setup.exe (one self-installing file).
# Usage: powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path .venv)) { py -3.12 -m venv .venv }
.venv\Scripts\python -m pip install -q -r requirements-desktop.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

.venv\Scripts\python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "tests failed" }

New-Item -ItemType Directory -Force build | Out-Null
.venv\Scripts\python -c "from knowledgeview.desktop.icon import save_ico; save_ico('build/app.ico')"
if ($LASTEXITCODE -ne 0) { throw "icon generation failed" }

# Pygments loads lexers/styles by name and uvicorn picks loops/protocols at run time,
# so their submodules are collected explicitly.
.venv\Scripts\pyinstaller --noconfirm --clean --onefile --windowed `
    --name KnowledgeView-Setup --icon build\app.ico `
    --add-data "knowledgeview\static;knowledgeview\static" `
    --add-data "knowledgeview\templates;knowledgeview\templates" `
    --add-data "THIRD_PARTY_NOTICES.md;." `
    --hidden-import pystray._win32 `
    --collect-submodules uvicorn `
    --collect-submodules pygments.lexers `
    --collect-submodules pygments.styles `
    --collect-submodules pygments.formatters `
    --exclude-module pytest `
    KnowledgeView.pyw
if ($LASTEXITCODE -ne 0) { throw "pyinstaller failed" }

Write-Host "Built: $PSScriptRoot\dist\KnowledgeView-Setup.exe"
