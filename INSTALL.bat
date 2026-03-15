@echo off
chcp 65001 >nul
title ⚡ SRT Translator - Cài đặt nhanh
color 0B

echo.
echo  ╔═══════════════════════════════════════════════════╗
echo  ║                                                   ║
echo  ║   ⚡ SRT TRANSLATOR - CÀI ĐẶT NHANH             ║
echo  ║   Dịch phụ đề SRT bằng AI                        ║
echo  ║                                                   ║
echo  ╚═══════════════════════════════════════════════════╝
echo.

:: ─── Kiểm tra Python ───
echo [1/4] Kiểm tra Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ❌ PYTHON CHƯA CÀI!
    echo.
    echo  👉 Bước 1: Tải Python tại: https://www.python.org/downloads/
    echo  👉 Bước 2: Khi cài, PHẢI tick ☑ "Add Python to PATH"
    echo  👉 Bước 3: Cài xong, chạy lại file này
    echo.
    echo  Bấm phím bất kỳ để mở trang tải Python...
    pause >nul
    start https://www.python.org/downloads/
    exit /b 1
)
echo  ✅ Python đã cài:
python --version
echo.

:: ─── Tạo virtual environment ───
echo [2/4] Tạo môi trường ảo...
if not exist ".venv" (
    python -m venv .venv
    echo  ✅ Đã tạo virtual environment
) else (
    echo  ✅ Virtual environment đã có sẵn
)
echo.

:: ─── Cài thư viện ───
echo [3/4] Cài thư viện cần thiết...
.venv\Scripts\pip.exe install -r requirements.txt -q
echo  ✅ Đã cài xong thư viện
echo.

:: ─── Tạo thư mục ───
echo [4/4] Tạo thư mục...
if not exist "temp_uploads" mkdir temp_uploads
if not exist "srt_in" mkdir srt_in
if not exist "srt_out" mkdir srt_out
if not exist "watch_input" mkdir watch_input
if not exist "watch_output" mkdir watch_output
if not exist "watch_done" mkdir watch_done
echo  ✅ Thư mục đã sẵn sàng
echo.

echo  ╔═══════════════════════════════════════════════════╗
echo  ║  ✅ CÀI ĐẶT HOÀN TẤT!                           ║
echo  ╚═══════════════════════════════════════════════════╝
echo.
echo  ┌─────────────────────────────────────────────────┐
echo  │  CÁCH SỬ DỤNG:                                  │
echo  │                                                  │
echo  │  🌐 CÁCH 1: Mở index.html bằng Chrome           │
echo  │     → Bấm ⚙️ nhập API Key → Kéo file .srt      │
echo  │     → Chọn ngôn ngữ → Bấm Dịch                 │
echo  │                                                  │
echo  │  🐍 CÁCH 2: Chạy start.bat (backend mạnh hơn)   │
echo  │     → Sửa config.py thêm API key trước          │
echo  │     → Chạy start.bat → tự mở trình duyệt       │
echo  │                                                  │
echo  │  🔑 LẤY API KEY:                                │
echo  │     Gemini: https://aistudio.google.com          │
echo  │     Groq:   https://console.groq.com/keys        │
echo  └─────────────────────────────────────────────────┘
echo.
echo  Bấm phím bất kỳ để đóng...
pause >nul
