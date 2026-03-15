@echo off
chcp 65001 >nul
echo Đang tắt SRT Translator...
taskkill /F /FI "WINDOWTITLE eq SRT-Backend*" >nul 2>&1
taskkill /F /FI "WINDOWTITLE eq SRT-Frontend*" >nul 2>&1
echo ✅ Đã tắt.
timeout /t 2 /nobreak >nul
