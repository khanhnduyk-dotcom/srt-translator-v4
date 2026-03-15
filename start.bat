@echo off
chcp 65001 >nul
title 🚀 SRT Translator

echo.
echo ╔══════════════════════════════════════════╗
echo ║   🚀 SRT TRANSLATOR - ĐANG KHỞI ĐỘNG   ║
echo ╚══════════════════════════════════════════╝
echo.

:: Tìm Python (ưu tiên .venv, sau đó system python)
if exist ".venv\Scripts\python.exe" (
    set PYTHON=.venv\Scripts\python.exe
) else (
    set PYTHON=python
)

:: Chạy launcher
%PYTHON% launcher.py

pause
