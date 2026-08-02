@echo off
setlocal enabledelayedexpansion
title FFmpeg 视频工具箱

REM ============================================
REM  FFmpeg 视频工具箱 - 统一启动脚本 (Windows)
REM ============================================

REM -- 获取脚本所在目录 --
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

REM -- 查找支持 tkinter 的 Python --
set "PYTHON="

python -c "import tkinter" >nul 2>&1
if %errorlevel% equ 0 (
    set "PYTHON=python"
    goto :found_py
)

py -c "import tkinter" >nul 2>&1
if %errorlevel% equ 0 (
    set "PYTHON=py"
    goto :found_py
)

for %%D in (Python310 Python311 Python312 Python313) do (
    if exist "%LOCALAPPDATA%\Programs\Python\%%D\python.exe" (
        "%LOCALAPPDATA%\Programs\Python\%%D\python.exe" -c "import tkinter" >nul 2>&1
        if !errorlevel! equ 0 (
            set "PYTHON=%LOCALAPPDATA%\Programs\Python\%%D\python.exe"
            goto :found_py
        )
    )
)

for %%D in (Python310 Python311 Python312 Python313) do (
    if exist "C:\%%D\python.exe" (
        "C:\%%D\python.exe" -c "import tkinter" >nul 2>&1
        if !errorlevel! equ 0 (
            set "PYTHON=C:\%%D\python.exe"
            goto :found_py
        )
    )
)

echo.
echo   [错误] 未找到支持 tkinter 的 Python 环境。
echo          请安装 Python 完整版（包含 tkinter）后重试。
echo.
pause
exit /b 1

:found_py

:menu
cls
echo.
echo   ============================================
echo            FFmpeg 视频工具箱  v1.0
echo   ============================================
echo.
echo   [1] 视频大小裁剪（裁剪画面区域 / 黑边）
echo   [2] 调整分辨率  （缩放 / 改变宽高）
echo   [3] 时间裁剪    （截取片段 / 起止时间）
echo   [0] 退出
echo.
echo   Python: %PYTHON%
echo.
set "choice="
set /p "choice=  请选择操作 [0-3]: "
echo.

if "%choice%"=="1" (
    set "RUN_SCRIPT=ffmpeg_crop_gui.py"
    set "RUN_NAME=视频大小裁剪"
    goto :run
)
if "%choice%"=="2" (
    set "RUN_SCRIPT=ffmpeg_resize_gui.py"
    set "RUN_NAME=调整分辨率"
    goto :run
)
if "%choice%"=="3" (
    set "RUN_SCRIPT=ffmpeg_clip_gui.py"
    set "RUN_NAME=时间裁剪"
    goto :run
)
if "%choice%"=="0" (
    echo   再见！
    echo.
    exit /b 0
)

echo   [警告] 无效选择，请输入 0-3。
timeout /t 1 >nul 2>&1
goto :menu

:run
if not exist "%SCRIPT_DIR%\%RUN_SCRIPT%" (
    echo   [错误] 找不到脚本文件：%RUN_SCRIPT%
    echo          期望路径：%SCRIPT_DIR%\%RUN_SCRIPT%
) else (
    echo   [启动] %RUN_NAME%（%RUN_SCRIPT%）...
    echo.
    "%PYTHON%" "%SCRIPT_DIR%\%RUN_SCRIPT%"
    echo.
    echo   [完成] 工具已退出。
)
echo.
pause
goto :menu
