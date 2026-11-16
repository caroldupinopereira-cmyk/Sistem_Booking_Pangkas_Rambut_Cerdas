@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m ruang_pangkas
) else (
    python -m ruang_pangkas
)
pause
