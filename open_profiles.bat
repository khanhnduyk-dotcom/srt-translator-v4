@echo off
chcp 65001 >nul
echo ================================
echo  Mở Chrome Profiles cho Cookie Pool
echo ================================
echo.

:: Tìm Chrome
set "CHROME="
if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" set "CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe"
if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" set "CHROME=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
if "%CHROME%"=="" (
    echo [LỖI] Không tìm thấy Chrome!
    pause
    exit /b 1
)

echo [1/2] Mở Profile Default (port 9222)...
start "" "%CHROME%" --profile-directory="Default" --remote-debugging-port=9222 --remote-allow-origins=* --restore-last-session

timeout /t 2 /nobreak >nul

echo [2/2] Mở Profile 2 (port 9223)...
start "" "%CHROME%" --profile-directory="Profile 1" --remote-debugging-port=9223 --remote-allow-origins=* --restore-last-session

echo.
echo ✅ Đã mở 2 Chrome profiles!
echo    Profile Default → port 9222
echo    Profile 2       → port 9223
echo.
echo Bấm "Auto" trong app để lấy cookie từ cả 2 profile.
echo.
pause
