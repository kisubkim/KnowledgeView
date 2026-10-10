@echo off
rem KnowledgeView 최초 설치: .venv 생성 후 의존성 설치
cd /d "%~dp0"
chcp 65001 >nul
if not exist .venv (
  py -3.12 -m venv .venv || python -m venv .venv || exit /b 1
)
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\pip install -r requirements-dev.txt || exit /b 1
echo.
echo 설치 완료. start.bat 으로 실행하세요.
