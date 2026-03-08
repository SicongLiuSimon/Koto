
#Requires -Version 5.1
<#
.SYNOPSIS
    Koto 高稳定性启动器 v3.0
.DESCRIPTION
    重试机制 · 端口冲突处理 · 孤进程清理 · 结构化日志 · 防重复启动
    支持命令行参数：
      -Mode   : desktop (默认) | server | silent
      -NoAutoRestart : 禁用崩溃后自动重启
      -MaxRetries N  : 最大重试次数 (默认 3)
#>

param(
    [ValidateSet("desktop","server","silent")]
    [string]$Mode = "desktop",
    [switch]$NoAutoRestart,
    [int]$MaxRetries = 3
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ─────────────────────────────────────────────
# 路径常量
# ─────────────────────────────────────────────
$candidateRoots = @(
    $PSScriptRoot,
    (Split-Path $PSScriptRoot -Parent)
) | Select-Object -Unique

$KOTO_ROOT = $candidateRoots |
    Where-Object {
        (Test-Path (Join-Path $_ "koto_setup.py")) -or
        (Test-Path (Join-Path $_ "koto_app.py")) -or
        (Test-Path (Join-Path $_ "server.py"))
    } |
    Select-Object -First 1

if ([string]::IsNullOrWhiteSpace($KOTO_ROOT)) {
    $KOTO_ROOT = $PSScriptRoot
}

$LOG_DIR     = Join-Path $KOTO_ROOT "logs"
$LOCK_FILE   = Join-Path $KOTO_ROOT ".koto.lock"
$LAUNCH_LOG  = Join-Path $LOG_DIR "launcher.log"
$WEB_APP     = Join-Path $KOTO_ROOT "web\app.py"

$KOTO_PORT   = [int]($env:KOTO_PORT -replace '\s','')
if ($KOTO_PORT -le 0) { $KOTO_PORT = 5000 }

function Resolve-EntryScript {
    param([string]$RunMode)

    $candidates = switch ($RunMode) {
        "server" {
            @(
                (Join-Path $KOTO_ROOT "server.py")
                (Join-Path $KOTO_ROOT "src\server.py")
            )
        }
        default {
            @(
                (Join-Path $KOTO_ROOT "koto_setup.py")
                (Join-Path $KOTO_ROOT "src\koto_setup.py")
                (Join-Path $KOTO_ROOT "koto_app.py")
                (Join-Path $KOTO_ROOT "src\koto_app.py")
            )
        }
    }

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    return $null
}

# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────
function Write-Log {
    param([string]$Level, [string]$Message)
    $ts  = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    $line = "[$ts][$Level] $Message"
    # 控制台颜色
    switch ($Level) {
        "INFO"  { Write-Host $line -ForegroundColor Cyan   }
        "OK"    { Write-Host $line -ForegroundColor Green  }
        "WARN"  { Write-Host $line -ForegroundColor Yellow }
        "ERROR" { Write-Host $line -ForegroundColor Red    }
        default { Write-Host $line }
    }
    # 写入日志文件
    try {
        if (-not (Test-Path $LOG_DIR)) { New-Item -ItemType Directory -Path $LOG_DIR -Force | Out-Null }
        Add-Content -Path $LAUNCH_LOG -Value $line -Encoding UTF8
    } catch { }
}

function Test-PortFree {
    param([int]$Port)
    try {
        $tcp = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
        $tcp.Start()
        $tcp.Stop()
        return $true
    } catch {
        return $false
    }
}

function Get-KotoProcesses {
    return Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "koto_app\.py" }
}

# ─────────────────────────────────────────────
# 防重复启动 (锁文件 + 进程检测)
# ─────────────────────────────────────────────
function Invoke-LockCheck {
    if (Test-Path $LOCK_FILE) {
        $lockedPid = (Get-Content $LOCK_FILE -ErrorAction SilentlyContinue).Trim()
        if ($lockedPid -match '^\d+$') {
            $proc = Get-Process -Id ([int]$lockedPid) -ErrorAction SilentlyContinue
            if ($null -ne $proc -and $proc.Name -match "python") {
                Write-Log "WARN" "已检测到运行中的 Koto 实例 (PID $lockedPid)。"
                Write-Log "WARN" "若需强制重启，请先运行 Stop_Koto.bat 或删除 .koto.lock"
                exit 1
            }
        }
        # 锁文件残留（上次崩溃）→ 清除
        Write-Log "WARN" "发现残留锁文件（上次可能异常退出），清理中..."
        Remove-Item $LOCK_FILE -Force -ErrorAction SilentlyContinue
    }
}

# ─────────────────────────────────────────────
# 孤进程清理
# ─────────────────────────────────────────────
function Clear-OrphanProcesses {
    # 优化：单次 WMI 批量查询，避免逐进程触发 Get-CimInstance（可节省数秒）
    try {
        $cimProcs = Get-CimInstance Win32_Process `
            -Filter "Name='python.exe' OR Name='pythonw.exe'" `
            -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -and $_.CommandLine -match "koto_app\.py" }
        if ($cimProcs) {
            $pids = @($cimProcs | ForEach-Object { $_.ProcessId })
            Write-Log "WARN" "清理孤立 Koto 进程: $($pids -join ', ')..."
            $pids | ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }
            Start-Sleep -Milliseconds 800
        }
    } catch { }
}

# ─────────────────────────────────────────────
# Python 环境检测
# ─────────────────────────────────────────────
function Find-Python {
    # 1. 虚拟环境 (pythonw 用于桌面无窗口, python 用于服务器)
    $venvPy     = Join-Path $KOTO_ROOT ".venv\Scripts\python.exe"
    $venvPyw    = Join-Path $KOTO_ROOT ".venv\Scripts\pythonw.exe"

    if (Test-Path $venvPyw) {
        return @{ Python = $venvPyw; PythonConsole = $venvPy; Source = "venv" }
    }
    if (Test-Path $venvPy) {
        return @{ Python = $venvPy; PythonConsole = $venvPy; Source = "venv-py" }
    }

    # 2. 系统 Python (py launcher > python)
    foreach ($cmd in @("py", "python3", "python")) {
        $resolved = Get-Command $cmd -ErrorAction SilentlyContinue
        if ($resolved) {
            $exePath = $resolved.Source
            # 尝试找同目录的 pythonw.exe
            $pyw = Join-Path (Split-Path $exePath) "pythonw.exe"
            if (Test-Path $pyw) {
                return @{ Python = $pyw; PythonConsole = $exePath; Source = "system" }
            }
            return @{ Python = $exePath; PythonConsole = $exePath; Source = "system" }
        }
    }
    return $null
}

function Assert-PythonVersion {
    param([string]$PythonExe)
    # 优化：缓存版本检查结果，避免每次启动都 spawn Python 子进程
    $cacheFile = Join-Path $LOG_DIR ".python_ver_cache"
    try {
        if (Test-Path $cacheFile) {
            $cached = Get-Content $cacheFile -ErrorAction SilentlyContinue
            # 格式: "<exe_path>|<version>"
            if ($cached -and $cached -match "^(.+)\|(.+)$") {
                if ($Matches[1] -eq $PythonExe) {
                    Write-Log "INFO" "Python 版本（缓存）: $($Matches[2])  ✓"
                    return $true
                }
            }
        }
    } catch { }
    try {
        $ver = & $PythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>&1
        if ($ver -match "^(\d+)\.(\d+)") {
            $major = [int]$Matches[1]; $minor = [int]$Matches[2]
            if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
                Write-Log "ERROR" "Python 版本过低: $ver（需要 3.10+）"
                return $false
            }
            Write-Log "INFO" "Python 版本: $ver  ✓"
            # 写入缓存
            try { Set-Content -Path $cacheFile -Value "$PythonExe|$ver" -Encoding ASCII } catch { }
            return $true
        }
    } catch { }
    Write-Log "WARN" "无法验证 Python 版本，继续尝试..."
    return $true
}

# ─────────────────────────────────────────────
# 端口冲突处理
# ─────────────────────────────────────────────
function Resolve-PortConflict {
    param([int]$Port)
    if (Test-PortFree -Port $Port) { return $Port }

    Write-Log "WARN" "端口 $Port 已被占用，尝试查找占用进程..."
    try {
        $netstat = netstat -ano 2>$null | Select-String ":$Port\s"
        if ($netstat) {
            $netstat | ForEach-Object {
                $cols = ($_ -split '\s+').Where({$_ -ne ''})
                $remotePid = $cols[-1]
                if ($remotePid -match '^\d+$') {
                    $proc = Get-Process -Id ([int]$remotePid) -ErrorAction SilentlyContinue
                    if ($proc -and $proc.CommandLine -match "koto") {
                        Write-Log "WARN" "杀死旧 Koto 进程 PID=$remotePid..."
                        Stop-Process -Id ([int]$remotePid) -Force -ErrorAction SilentlyContinue
                        Start-Sleep -Seconds 1
                    } else {
                        Write-Log "WARN" "端口 $Port 被非 Koto 进程占用 (PID $remotePid, $($proc.Name))"
                    }
                }
            }
        }
    } catch { }

    # 尝试备用端口
    $fallback = $Port + 1
    if (Test-PortFree -Port $fallback) {
        Write-Log "INFO" "使用备用端口: $fallback"
        $env:KOTO_PORT = "$fallback"
        return $fallback
    }

    Write-Log "ERROR" "端口 $Port 和 $fallback 均不可用，无法启动"
    exit 2
}

# ─────────────────────────────────────────────
# 必要文件检查
# ─────────────────────────────────────────────
function Assert-RequiredFiles {
    param([string]$RunMode)

    $entryScript = Resolve-EntryScript -RunMode $RunMode
    $required = @($entryScript, $WEB_APP)
    foreach ($f in $required) {
        if ([string]::IsNullOrWhiteSpace($f) -or -not (Test-Path $f)) {
            Write-Log "ERROR" "缺少必要文件: $f"
            exit 3
        }
    }
    # 确保目录存在
    foreach ($dir in @("logs","chats","workspace","config")) {
        $dirPath = Join-Path $KOTO_ROOT $dir
        if (-not (Test-Path $dirPath)) {
            New-Item -ItemType Directory -Path $dirPath -Force | Out-Null
        }
    }
}

# ─────────────────────────────────────────────
# 主启动逻辑（带重试）
# ─────────────────────────────────────────────
function Start-KotoApp {
    param(
        [hashtable]$PythonInfo,
        [int]$Port,
        [string]$Mode
    )

    $entryScript = Resolve-EntryScript -RunMode $Mode
    if ([string]::IsNullOrWhiteSpace($entryScript)) {
        Write-Log "ERROR" "未找到可用入口脚本 (Mode=$Mode)"
        exit 3
    }

    $retryCount = 0
    $backoffSec  = 3

    while ($true) {
        Write-Log "INFO" "============================================"
        Write-Log "INFO" "启动 Koto  模式=$Mode  端口=$Port  重试=$retryCount"
        Write-Log "INFO" "Python: $($PythonInfo.Source) → $($PythonInfo.PythonConsole)"
        Write-Log "INFO" "Entry: $entryScript"
        Write-Log "INFO" "============================================"

        # 写锁文件（稍后填入 PID）
        Set-Content -Path $LOCK_FILE -Value "starting" -Encoding ASCII

        # 根据模式选择 Python 可执行文件
        $useExe = switch ($Mode) {
            "desktop" { $PythonInfo.Python }        # pythonw.exe → 无控制台窗口
            "server"  { $PythonInfo.PythonConsole } # python.exe  → 保留控制台
            "silent"  { $PythonInfo.Python }
            default   { $PythonInfo.Python }
        }

        # 环境变量透传
        $env:KOTO_PORT = "$Port"
        if ($Mode -eq "server") { $env:KOTO_DEPLOY_MODE = "local" }

        # 日志文件（每次重试追加）
        $runtimeLog = Join-Path $LOG_DIR ("runtime_" + (Get-Date -Format "yyyyMMdd") + ".log")
        $errLog     = Join-Path $LOG_DIR "server_latest_err.log"

        try {
            if ($Mode -eq "server") {
                # 服务器模式：前台运行，输出重定向
                $proc = Start-Process -FilePath $useExe `
                    -ArgumentList "`"$entryScript`"" `
                    -WorkingDirectory $KOTO_ROOT `
                    -PassThru `
                    -NoNewWindow `
                    -RedirectStandardOutput $runtimeLog `
                    -RedirectStandardError  $errLog
            } else {
                # 桌面/静默模式：后台运行
                $proc = Start-Process -FilePath $useExe `
                    -ArgumentList "`"$entryScript`"" `
                    -WorkingDirectory $KOTO_ROOT `
                    -PassThru
            }

            Set-Content -Path $LOCK_FILE -Value "$($proc.Id)" -Encoding ASCII
            Write-Log "OK"   "Koto 已启动  PID=$($proc.Id)"

            if ($Mode -eq "server") {
                Write-Log "INFO" "浏览器访问: http://127.0.0.1:$Port"
                Write-Log "INFO" "按 Ctrl+C 停止服务"

                # 注册 Ctrl+C 优雅退出
                [Console]::TreatControlCAsInput = $false
                $null = Register-EngineEvent -SourceIdentifier PowerShell.Exiting -Action {
                    if (Test-Path $LOCK_FILE) { Remove-Item $LOCK_FILE -Force -ErrorAction SilentlyContinue }
                }

                $proc.WaitForExit()
                $exitCode = $proc.ExitCode
            } else {
                # 桌面模式：等待最多 5s 确认进程未立即崩溃（优化：原来等15s）
                $waited = 0
                while ($waited -lt 5) {
                    Start-Sleep -Seconds 1
                    $waited++
                    if ($proc.HasExited) { break }
                }

                if (-not $proc.HasExited) {
                    # 桌面模式：进程健康，启动器立即退出（Koto 继续运行）
                    Remove-Item $LOCK_FILE -Force -ErrorAction SilentlyContinue
                    Set-Content -Path $LOCK_FILE -Value "$($proc.Id)" -Encoding ASCII
                    Write-Log "OK" "Koto 正在后台运行 (PID=$($proc.Id))。关闭本窗口不会停止 Koto。"
                    exit 0
                }
                $exitCode = $proc.ExitCode
            }

            Write-Log "WARN" "Koto 进程退出，退出码: $exitCode"

        } catch {
            Write-Log "ERROR" "启动失败: $_"
            $exitCode = -1
        }

        # 清理锁文件
        Remove-Item $LOCK_FILE -Force -ErrorAction SilentlyContinue

        # 重试判断
        if ($NoAutoRestart) {
            Write-Log "INFO" "自动重启已禁用，退出。"
            exit 1
        }
        if ($retryCount -ge $MaxRetries) {
            Write-Log "ERROR" "已达最大重试次数 ($MaxRetries)，放弃启动。"
            Write-Log "ERROR" "请检查日志: $runtimeLog  $errLog"
            exit 1
        }

        $retryCount++
        Write-Log "WARN" "等待 $backoffSec 秒后重试 ($retryCount/$MaxRetries)..."
        Start-Sleep -Seconds $backoffSec
        $backoffSec = [Math]::Min($backoffSec * 2, 30)  # 指数退避，最大 30s
    }
}

# ─────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────
Set-Location $KOTO_ROOT

Write-Host ""
Write-Host "  ██╗  ██╗ ██████╗ ████████╗ ██████╗ " -ForegroundColor Magenta
Write-Host "  ██║ ██╔╝██╔═══██╗╚══██╔══╝██╔═══██╗" -ForegroundColor Magenta
Write-Host "  █████╔╝ ██║   ██║   ██║   ██║   ██║" -ForegroundColor Magenta
Write-Host "  ██╔═██╗ ██║   ██║   ██║   ██║   ██║" -ForegroundColor Magenta
Write-Host "  ██║  ██╗╚██████╔╝   ██║   ╚██████╔╝" -ForegroundColor Magenta
Write-Host "  ╚═╝  ╚═╝ ╚═════╝    ╚═╝    ╚═════╝ " -ForegroundColor Magenta
Write-Host ""
Write-Host "  智能文档处理平台  |  Launcher v3.0" -ForegroundColor White
Write-Host ""

Write-Log "INFO" "启动模式: $Mode | 根目录: $KOTO_ROOT"

# Step 1: 防重复
Invoke-LockCheck

# Step 2: 清孤进程
Clear-OrphanProcesses

# Step 3: 文件检查
Assert-RequiredFiles -RunMode $Mode

# Step 4: 检测 Python
$pyInfo = Find-Python
if ($null -eq $pyInfo) {
    Write-Log "ERROR" "未找到 Python 环境！"
    Write-Log "ERROR" "请安装 Python 3.10+ 或在项目目录下创建 .venv 虚拟环境。"
    Write-Log "ERROR" "安装命令: python -m venv .venv && .venv\Scripts\pip install -r requirements.txt"
    Read-Host "按 Enter 退出"
    exit 4
}

$pyConsole = $pyInfo.PythonConsole
if (-not (Assert-PythonVersion -PythonExe $pyConsole)) {
    Read-Host "按 Enter 退出"
    exit 5
}

# Step 5: 端口检查
$resolvedPort = Resolve-PortConflict -Port $KOTO_PORT

# Step 6: 启动（含重试）
Start-KotoApp -PythonInfo $pyInfo -Port $resolvedPort -Mode $Mode
