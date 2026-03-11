#Requires -Version 5.1
<#
.SYNOPSIS
    Koto 一键打包发布脚本
.DESCRIPTION
    功能：
      1. 运行 PyInstaller 构建 Koto.exe（dist/Koto/）
      2. 运行 deploy_portable.py 组装便携包（dist/Koto_Portable/）
      3. 将便携包压缩为带版本号的 zip（dist/Koto_v*.zip）

    使用方法：
      .\Build_Release.ps1                    # 正常构建（含 --clean，完整重建）
      .\Build_Release.ps1 -Incremental       # 增量构建：跳过 --clean，只重编译变更的 .py
      .\Build_Release.ps1 -SkipBuild         # 跳过 PyInstaller，直接重打包（仅资源/配置变动时用）
      .\Build_Release.ps1 -Version "1.2.0"   # 指定版本号（默认读根目录 VERSION 文件）

    常见问题：
      - ModuleNotFoundError  → 在 koto.spec 的 hiddenimports 里补模块名
      - 找不到资源文件        → 在 koto.spec 的 datas 里补路径
      - 启动崩溃无提示       → 查看 logs/ 目录的日志文件
#>

param(
    [switch]$SkipBuild,
    [switch]$Incremental,   # 增量构建：不加 --clean，保留上次缓存（只改了 .py 时快很多）
    [string]$Version = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$REPO_ROOT  = $PSScriptRoot
$VENV_PIP   = Join-Path $REPO_ROOT ".venv\Scripts\pyinstaller.exe"
$DEPLOY_PY  = Join-Path $REPO_ROOT "src\deploy_portable.py"
$PYTHON     = Join-Path $REPO_ROOT ".venv\Scripts\python.exe"
$DIST_DIR   = Join-Path $REPO_ROOT "dist"
$LOG_DIR    = Join-Path $REPO_ROOT "logs"
$SPEC_FILE  = Join-Path $REPO_ROOT "koto.spec"

# ─── 颜色输出辅助 ─────────────────────────────
function Write-Step  { param([string]$msg) Write-Host "`n[$([char]0x25B6)] $msg" -ForegroundColor Cyan }
function Write-OK    { param([string]$msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Fail  { param([string]$msg) Write-Host "  [!!] $msg" -ForegroundColor Red }

# ─── 前置检查 ────────────────────────────────
Write-Step "前置检查"
if (-not (Test-Path $VENV_PIP)) {
    Write-Fail "找不到 .venv\Scripts\pyinstaller.exe，请先运行：python -m venv .venv ; .\.venv\Scripts\pip install -r config\requirements.txt"
    exit 1
}
if (-not (Test-Path $PYTHON)) {
    Write-Fail "找不到 .venv\Scripts\python.exe"
    exit 1
}
if (-not (Test-Path $LOG_DIR)) { New-Item -ItemType Directory -Path $LOG_DIR | Out-Null }
Write-OK "虚拟环境 OK"

# ─── 版本号（单一来源：根目录 VERSION 文件）──────────
if ([string]::IsNullOrWhiteSpace($Version)) {
    $versionFile = Join-Path $REPO_ROOT "VERSION"
    if (Test-Path $versionFile) {
        $Version = (Get-Content $versionFile -Raw).Trim()
    }
}
if ([string]::IsNullOrWhiteSpace($Version)) { $Version = Get-Date -Format "yyyy.MM.dd" }
Write-OK "版本号: $Version"

# ─── 步骤 1：PyInstaller 构建 ─────────────────
if (-not $SkipBuild) {
    $buildLog = Join-Path $LOG_DIR "build_latest.log"
    if ($Incremental) {
        Write-Step "步骤 1/3  PyInstaller 增量构建（无 --clean，输出日志至 logs\build_latest.log）"
        & $VENV_PIP $SPEC_FILE -y *> $buildLog
    } else {
        Write-Step "步骤 1/3  PyInstaller 完整构建（--clean，输出日志至 logs\build_latest.log）"
        & $VENV_PIP $SPEC_FILE --clean -y *> $buildLog
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "PyInstaller 失败，查看详细日志：$buildLog"
        Write-Host "(最后 30 行)" -ForegroundColor Yellow
        Get-Content $buildLog -Tail 30
        exit 1
    }
    Write-OK "构建完成 → dist\Koto\Koto.exe"
} else {
    Write-Step "跳过 PyInstaller（-SkipBuild）"
}

# ─── 步骤 2：组装便携包 ───────────────────────
Write-Step "步骤 2/3  组装便携包（dist\Koto_Portable\）"
& $PYTHON $DEPLOY_PY --allow-missing-installer
if ($LASTEXITCODE -ne 0) {
    Write-Fail "deploy_portable.py 失败"
    exit 1
}
Write-OK "便携包已组装 → dist\Koto_Portable\"

# ─── 步骤 3：压缩为 zip ───────────────────────
Write-Step "步骤 3/3  压缩为 zip"
$zipName = "Koto_v${Version}_Windows.zip"
$zipPath = Join-Path $DIST_DIR $zipName
$portableDir = Join-Path $DIST_DIR "Koto_Portable"

if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Compress-Archive -Path "$portableDir\*" -DestinationPath $zipPath -CompressionLevel Optimal
Write-OK "zip 已生成 → dist\$zipName"

# ─── 完成 ─────────────────────────────────────
Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host "  打包完成！发布文件：dist\$zipName" -ForegroundColor Green
Write-Host "  用户使用方法：解压 → 填写 API Key → 双击 Start_Koto.bat" -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
