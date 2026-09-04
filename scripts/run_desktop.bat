@chcp 65001 >nul
@echo off
rem 一键启动 Retrostation 桌面版（Windows）。双击即可运行，无需手动设置环境。
rem 直接调用 python（不经 PowerShell），并把每一步打印出来，方便排查问题。
setlocal
cd /d "%~dp0.."

rem 强制 Python 以 UTF-8 进行标准流 I/O，避免中文打印乱码。
set "PYTHONUTF8=1"
set "PYTHONPATH=%CD%\src"
set PYTHONUNBUFFERED=1

echo ============================================================
echo   Retrostation 桌面版启动器
echo   项目根目录 : %CD%
echo   PYTHONPATH : %PYTHONPATH%
echo ============================================================

rem 选一个可用的 Python 解释器（python 优先，回退到 py 启动器）。
set "PY=python"
where python >nul 2>nul || set "PY=py"
where %PY% >nul 2>nul
if errorlevel 1 (
    echo [错误] 找不到 python 或 py。请先安装 Python 3.11+ 并勾选加入 PATH。
    pause
    exit /b 1
)

echo [1/2] 检查 tkinter（桌面窗口依赖）...
%PY% -c "import tkinter" 2>nul
if errorlevel 1 (
    echo [错误] 当前 Python 缺少 tkinter（标准库的一部分）。
    echo         请重新运行官方安装器，并在 "Optional Features" 中勾选 tcl/tk and IDLE。
    pause
    exit /b 1
)

echo [2/2] 启动 Retrostation（--desktop）...
echo         关闭窗口 或 按 Alt+F4 退出。
echo -----------------------------------------------------------------

rem 默认 ROM 目录（含中文路径）等逻辑交给 run_desktop.ps1 处理。
rem cmd 直接读含中文的 .bat 会按 GBK 乱码，导致 --rom-root 根本传不进去；
rem PowerShell 对 UTF-8 / 中文路径可靠，且 .ps1 已正确处理默认目录。
echo [提示] 正在通过 run_desktop.ps1 启动（支持中文 ROM 路径）……
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0run_desktop.ps1" %*
set "CODE=%errorlevel%"
echo -----------------------------------------------------------------
if not "%CODE%"=="0" (
    echo [退出] 程序异常退出，退出码: %CODE%
) else (
    echo [退出] 正常结束。
)
pause
