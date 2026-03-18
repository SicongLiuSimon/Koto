<#
.SYNOPSIS
    End-to-end test for the Koto portable ZIP bundle (Koto_v*_Windows.zip).

    Steps:
      1. Extract ZIP to temp dir
      2. Verify critical files + file size + Python DLL
      3. Seed config (bypass first-run wizard) + launch
      4. Poll /api/health + /api/ping
      5. Stop process
      6. Remove temp dir

    Exit 0 on success, 1 on any failure.

.PARAMETER ZipFile
    Path to Koto_v*_Windows.zip. Defaults to searching dist\.

.PARAMETER Port
    Port for the test server. Default 5098.

.PARAMETER HealthTimeoutSec
    Seconds to wait for /api/health. Default 45.

.EXAMPLE
    .\test_portable_e2e.ps1 -ZipFile "C:\downloads\Koto_v1.0.3_Windows.zip"
#>
param(
    [string]$ZipFile          = "",
    [int]$Port                = 5098,
    [int]$HealthTimeoutSec    = 45,
    [switch]$RequireHealth    = $true   # set to $false in headless/CI
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Resolve-Path (Join-Path $ScriptDir "..\..")
$ExtractDir = Join-Path $env:TEMP "KotoPortableE2E"

# ── Locate ZIP ───────────────────────────────────────────────────────────
if (-not $ZipFile) {
    $found = Get-Item (Join-Path $RepoRoot "dist\Koto_v*_Windows.zip") -ErrorAction SilentlyContinue |
             Select-Object -First 1
    if ($found) { $ZipFile = $found.FullName }
}
if (-not $ZipFile -or -not (Test-Path $ZipFile)) {
    Write-Error "ERROR: ZIP not found. Pass -ZipFile <path>."
    exit 1
}
Write-Host "[E2E-Portable] ZIP: $ZipFile"
Write-Host "[E2E-Portable] Extract dir: $ExtractDir"
Write-Host "[E2E-Portable] Port: $Port"

$failures = [System.Collections.Generic.List[string]]::new()
function Fail([string]$msg) { $script:failures.Add($msg); Write-Host "::error:: FAIL: $msg" }
function Pass([string]$msg) { Write-Host "  PASS: $msg" }

# ── Cleanup leftovers ────────────────────────────────────────────────────
if (Test-Path $ExtractDir) {
    Remove-Item $ExtractDir -Recurse -Force -ErrorAction SilentlyContinue
}

# ══════════════════════════════════════════════════════════════════════════
# STEP 1 — Extract ZIP
# ══════════════════════════════════════════════════════════════════════════
Write-Host "`n[Step 1] Extracting ZIP..."
Expand-Archive -Path $ZipFile -DestinationPath $ExtractDir -Force
Pass "ZIP extracted to $ExtractDir"

# ══════════════════════════════════════════════════════════════════════════
# STEP 2 — Verify critical files
# ══════════════════════════════════════════════════════════════════════════
Write-Host "`n[Step 2] Verifying extracted files..."
$exePath = Join-Path $ExtractDir "Koto.exe"
$internalDir = Join-Path $ExtractDir "_internal"

$requiredPaths = @(
    $exePath,
    $internalDir,
    (Join-Path $internalDir "psutil"),
    (Join-Path $internalDir "app"),
    (Join-Path $internalDir "web"),
    (Join-Path $ExtractDir "Start_Koto.bat")
)
foreach ($path in $requiredPaths) {
    if (Test-Path $path) { Pass "Exists: $(Split-Path -Leaf $path)" }
    else                 { Fail "Missing: $path" }
}

# File size validation — catch empty or corrupt builds
$exeSize = (Get-Item $exePath).Length / 1MB
if ($exeSize -lt 50) { Fail "Koto.exe is only $([math]::Round($exeSize,1))MB (expected >= 50MB)" }
else                  { Pass "Koto.exe size is $([math]::Round($exeSize,1))MB" }

# Critical DLL check — Python runtime must be present
$pythonDll = Join-Path $internalDir "python311.dll"
if (Test-Path $pythonDll) { Pass "python311.dll exists in _internal" }
else                      { Fail "python311.dll missing from _internal" }

# ══════════════════════════════════════════════════════════════════════════
# STEP 3 — Seed config + launch
# ══════════════════════════════════════════════════════════════════════════
Write-Host "`n[Step 3] Seeding config and launching..."
& (Join-Path $ScriptDir "seed_config.ps1") -InstallDir $ExtractDir

$env:KOTO_PORT = $Port
if ($env:KOTO_SERVER_ONLY) { Write-Host "  KOTO_SERVER_ONLY=$env:KOTO_SERVER_ONLY (server-only mode)" }
$kotoProc = Start-Process -FilePath $exePath `
    -WorkingDirectory $ExtractDir `
    -PassThru

Write-Host "  Koto.exe PID: $($kotoProc.Id)"

# ══════════════════════════════════════════════════════════════════════════
# STEP 4 — Poll /api/health
# ══════════════════════════════════════════════════════════════════════════
Write-Host "`n[Step 4] Waiting for http://localhost:$Port/api/health (up to ${HealthTimeoutSec}s)..."
$healthUrl = "http://localhost:$Port/api/health"
$deadline  = (Get-Date).AddSeconds($HealthTimeoutSec)
$healthy   = $false

while ((Get-Date) -lt $deadline) {
    if ($kotoProc.HasExited) {
        Fail "Koto.exe exited unexpectedly (code $($kotoProc.ExitCode))"
        break
    }
    try {
        $resp = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 3 -ErrorAction Stop
        if ($resp.success -eq $true -or $resp.status -eq "ok" -or $resp.status -eq "healthy") {
            $healthy = $true; Pass "/api/health returned success"; break
        }
    } catch {}
    # Fallback: accept any 200
    try {
        $raw = Invoke-WebRequest -Uri $healthUrl -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
        if ($raw.StatusCode -eq 200) {
            $healthy = $true; Pass "/api/health HTTP 200"; break
        }
    } catch {}
    Start-Sleep -Milliseconds 1000
}
if (-not $healthy) {
    if ($RequireHealth) {
        Fail "Health endpoint did not respond within ${HealthTimeoutSec}s"
    } else {
        Write-Host "::warning::Health endpoint did not respond within ${HealthTimeoutSec}s (best-effort in CI — pywebview may not init headless)"
    }
}

# /api/ping endpoint check
if ($healthy) {
    try {
        $pingResp = Invoke-RestMethod "http://localhost:$Port/api/ping" -TimeoutSec 5
        Pass "/api/ping responded"
    } catch {
        Write-Host "  WARN: /api/ping did not respond"
    }
}

# ══════════════════════════════════════════════════════════════════════════
# STEP 5 — Stop process
# ══════════════════════════════════════════════════════════════════════════
Write-Host "`n[Step 5] Stopping Koto.exe..."
if (-not $kotoProc.HasExited) {
    Stop-Process -Id $kotoProc.Id -Force -ErrorAction SilentlyContinue
    $kotoProc.WaitForExit(5000) | Out-Null
    Pass "Process stopped"
}

# ══════════════════════════════════════════════════════════════════════════
# STEP 6 — Cleanup
# ══════════════════════════════════════════════════════════════════════════
Write-Host "`n[Step 6] Removing temp dir..."
Start-Sleep -Seconds 1
Remove-Item $ExtractDir -Recurse -Force -ErrorAction SilentlyContinue
if (-not (Test-Path $ExtractDir)) { Pass "Temp dir removed" }
else                              { Fail "Temp dir still present after cleanup" }

# ══════════════════════════════════════════════════════════════════════════
# RESULT
# ══════════════════════════════════════════════════════════════════════════
Write-Host ""
if ($failures.Count -eq 0) {
    Write-Host "✅ Portable E2E: ALL CHECKS PASSED" -ForegroundColor Green
    exit 0
} else {
    Write-Host "❌ Portable E2E: $($failures.Count) CHECK(S) FAILED:" -ForegroundColor Red
    $failures | ForEach-Object { Write-Host "   - $_" -ForegroundColor Red }
    exit 1
}
