@echo off
:: Koto 停止脚本 v3.0
setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
set "KOTO_ROOT=%SCRIPT_DIR%"
if not exist "%KOTO_ROOT%koto_setup.py" if not exist "%KOTO_ROOT%Koto.exe" if not exist "%KOTO_ROOT%web\app.py" (
    for %%I in ("%SCRIPT_DIR%..") do set "KOTO_ROOT=%%~fI\"
)

cd /d "%KOTO_ROOT%"
title Koto Stopper

echo.
echo  [Koto] 正在停止...

set "LOCK_FILE=%KOTO_ROOT%.koto.lock"
set "KILLED=0"

:: 1. 从锁文件读取 PID（精准杀进程）
if exist "%LOCK_FILE%" (
    set /p LOCKED_PID=<"%LOCK_FILE%"
    if defined LOCKED_PID (
        if "!LOCKED_PID!" neq "starting" (
            taskkill /F /PID !LOCKED_PID! >nul 2>&1
            if not errorlevel 1 (
                echo  [OK] 已终止进程 PID=!LOCKED_PID!
                set "KILLED=1"
            )
        )
    )
    del /F "%LOCK_FILE%" >nul 2>&1
)

:: 2. 兜底：查找所有 koto_app.py 进程
for /f "tokens=2 delims==" %%i in ('wmic process where "CommandLine like ''%%koto_app.py%%''" get ProcessId /value 2^>nul ^| find "="') do (
    set "PID_VAL=%%i"
    taskkill /F /PID !PID_VAL! >nul 2>&1
    echo  [OK] 已终止关联进程 PID=!PID_VAL!
    set "KILLED=1"
)

if "!KILLED!"=="1" (
    echo  [OK] Koto 已成功停止。
) else (
    echo  [INFO] 未检测到运行中的 Koto 进程。
)

echo.
timeout /t 2 >nul
endlocal
exit /b 0
