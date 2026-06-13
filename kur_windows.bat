@echo off
rem ArduCheck kurulum betigi (Windows)
cd /d "%~dp0"
echo == ArduCheck kurulumu ==

where python >nul 2>nul
if errorlevel 1 (
    echo HATA: Python bulunamadi.
    echo https://www.python.org/downloads/ adresinden Python 3 kurun.
    echo Kurulumda "Add Python to PATH" kutusunu isaretlemeyi unutmayin!
    pause
    exit /b 1
)

if not exist .venv (
    python -m venv .venv
)
.venv\Scripts\python -m pip install --upgrade pip -q
.venv\Scripts\python -m pip install -r requirements.txt -q
if errorlevel 1 (
    echo.
    echo HATA: Kurulum basarisiz. Internet baglantinizi kontrol edip
    echo betigi tekrar calistirin.
    pause
    exit /b 1
)

echo.
echo Kurulum tamam. Baslatmak icin: baslat_windows.bat
pause
