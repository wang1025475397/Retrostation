<#
Desktop launcher for Retrostation on Windows -- runs the frontend with a
tkinter window + keyboard instead of the handheld's SDL/evdev stack.
Mirrors scripts/retrostation.sh but without the on-device game-launch loop
(the desktop build stays resident and never emits exit code 42/43).

Usage:
  .\run_desktop.ps1                      # default: --desktop
  .\run_desktop.ps1 --rom-root D:\Roms   # point at your own ROM directory
  .\run_desktop.ps1 --single            # single screen instead of dual

Any extra arguments are forwarded to retrostation.main.
#>
$ErrorActionPreference = 'Stop'

# Make the PowerShell console speak UTF-8 so Chinese output is not garbled.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here

# retrostation is laid out as src/retrostation, so the package root is src/.
$src = Join-Path $root 'src'
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$src$([IO.Path]::PathSeparator)$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = $src
}
$env:PYTHONUNBUFFERED = '1'

# Pick a Python interpreter.
$py = $null
foreach ($cand in 'python', 'py', 'python3') {
    if (Get-Command $cand -ErrorAction SilentlyContinue) { $py = $cand; break }
}
if (-not $py) {
    Write-Error "找不到 Python。请先安装 Python 3.11+ 并确保它在 PATH 中。"
    Read-Host "按回车退出"
    exit 1
}

# tkinter ships with the official Windows installer; the desktop backend needs it.
& $py -c "import tkinter" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Error "当前 Python 缺少 tkinter（标准库的一部分）。请重新运行官方安装器并勾选 tcl/tk 组件。"
    Read-Host "按回车退出"
    exit 1
}

# Default to the desktop platform unless the caller already selected it.
$args2 = @()
if ($args -notcontains '--desktop') { $args2 += '--desktop' }

# Auto-use the default ROM directory unless the caller overrode it.
# The path lives in rom_root.txt (next to this script) -- edit only THAT file
# to change where your ROMs are; a command-line --rom-root still wins.
$romRootConfig = Join-Path $here 'rom_root.txt'
$defaultRomRoot = $null
if (Test-Path $romRootConfig) {
    # UTF-8 read so Chinese paths survive on Windows PowerShell 5.1 too.
    $defaultRomRoot = (Get-Content -Encoding UTF8 -Path $romRootConfig |
        Where-Object { $_.Trim() -and -not $_.Trim().StartsWith('#') } |
        Select-Object -First 1).Trim()
}
if (-not $defaultRomRoot) {
    # Built-in fallback only when the config file is missing or empty.
    $defaultRomRoot = 'F:\天马精简包\Roms'
}
if (-not ($args | Where-Object { $_ -like '--rom-root*' }) -and $defaultRomRoot -and (Test-Path $defaultRomRoot)) {
    $args2 += '--rom-root', $defaultRomRoot
}
$args2 += $args

Write-Host "Retrostation (desktop) 启动中…… 关闭窗口或按 Ctrl+C 退出。"
& $py -m retrostation.main @args2
$code = $LASTEXITCODE
if ($code -ne 0) {
    Write-Host "程序退出码: $code"
    Read-Host "按回车退出"
}
exit $code
