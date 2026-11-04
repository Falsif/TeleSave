@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === TeleSave: сборка программы и установщика ===

where python >nul 2>nul
if errorlevel 1 (
    echo Python не найден. Установите Python 3.12 с python.org ^(галочка "Add python.exe to PATH"^).
    pause
    exit /b 1
)

if not exist .venv (
    python -m venv .venv || goto :err
)
call .venv\Scripts\activate.bat || goto :err
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller || goto :err

python build.py || goto :err

if exist output explorer output
pause
exit /b 0

:err
echo.
echo Сборка завершилась с ошибкой. Пришлите текст выше, если нужна помощь.
pause
exit /b 1
