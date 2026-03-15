@echo off
chcp 65001 >nul
title 🔧 Cài đặt SRT Translator
echo.
echo ╔══════════════════════════════════════════╗
echo ║   CÀI ĐẶT SRT TRANSLATOR               ║
echo ╚══════════════════════════════════════════╝
echo.

:: Kiểm tra Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Python chưa được cài đặt!
    echo.
    echo 👉 Tải Python tại: https://www.python.org/downloads/
    echo    Nhớ tick "Add Python to PATH" khi cài.
    echo.
    pause
    exit /b 1
)

echo ✅ Python đã cài đặt
python --version
echo.

:: Tạo virtual environment
if not exist ".venv" (
    echo 📦 Đang tạo virtual environment...
    python -m venv .venv
    echo ✅ Virtual environment đã tạo
) else (
    echo ✅ Virtual environment đã có sẵn
)
echo.

:: Cài thư viện
echo 📦 Đang cài thư viện cần thiết...
.venv\Scripts\pip.exe install -r requirements.txt -q
echo ✅ Đã cài xong thư viện
echo.

:: Tạo thư mục cần thiết
if not exist "temp_uploads" mkdir temp_uploads
if not exist "srt_in" mkdir srt_in
if not exist "srt_out" mkdir srt_out

echo ✅ Cài đặt hoàn tất!
echo.
echo 👉 Bây giờ chạy: start.bat
echo.
pause
