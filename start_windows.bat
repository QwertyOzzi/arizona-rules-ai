@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .env (
    copy .env.example .env >nul
    echo [Arizona Rules AI] Создан файл .env. В нём нужно сменить APP_PASSWORD и APP_SECRET,
    echo а для ответов ИИ добавить GEMINI_API_KEY.
    notepad .env
)
if not exist .venv (
    py -3 -m venv .venv
    if errorlevel 1 python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo Не удалось установить зависимости.
    pause
    exit /b 1
)
echo.
echo Открой в браузере http://127.0.0.1:8000
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --env-file .env
pause
