@echo off
rem KnowledgeView 실행. 다른 PC에서 접속하려면 KV_HOST=0.0.0.0 (기본값)
rem   KV_HOST, KV_PORT(비우면 웹 설정 > 서버에서 저장한 포트, 기본 8770), KV_ADMIN_TOKEN, KV_DATA_DIR
cd /d "%~dp0"
chcp 65001 >nul
if "%KV_HOST%"=="" set KV_HOST=0.0.0.0
.venv\Scripts\python -m knowledgeview --host %KV_HOST%
