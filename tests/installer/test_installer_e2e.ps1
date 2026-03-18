<#
.SYNOPSIS
    End-to-end test for the Koto Windows installer (Koto_v*_Setup.exe).

    Steps:
      1. Silent install to $TestInstallDir
      2. Verify critical files + file size + Start Menu shortcut
      3. Verify Windows registry key written by Inno Setup
      4. Seed config (bypass first-run wizard) + launch Koto.exe
      5. Poll /api/health + /api/ping
      6. Stop Koto process
      7. Silent uninstall
      8. Verify cleanup (files + registry key removed)
      9. Reinstall (upgrade scenario)
     10. Second uninstall + verify cleanup

    Exit 0 on success, 1 on any failure.

.PARAMETER SetupExe
    Path to Koto_v*_Setup.exe.  Defaults to searching dist\ then the script dir.

.PARAMETER TestInstallDir
    Where to install for testing.  Defaults to $env:LOCALAPPDATA\KotoE2ETest.

.PARAMETER Port
    Port for the test server.  Default 5099.

.PARAMETER HealthTimeoutSec
    Seconds to wait for /api/health before failing.  Default 45.

.EXAMPLE
    .\test_installer_e2e.ps1 -SetupExe "C:\downloads\Koto_v1.0.3_Setup.exe"
#>
param(
    [string]$SetupExe       = "",
    [string]$TestInstallDir = "$env:LOCALAPPDATA\KotoE2ETest",
    [int]$Port              = 5099,
    [int]$HealthTimeoutSec  = 45,
    [switch]$RequireHealth  = $true   # set to $false in headless/CI to treat as warning
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Resolve-Path (Join-Path $ScriptDir "..\..")

# ── Locate installer ────────────────────────────────────────────────────
if (-not $SetupExe) {
    $candidates = @(
        (Join-Path $RepoRoot "dist\Koto_v*_Setup.exe"),
        (Join-Path $ScriptDir "Koto_v*_Setup.exe")
    )
    foreach ($c in $candidates) {
        $found = Get-Item $c -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($found) { $SetupExe = $found.FullName; break }
    }
}
if (-not $SetupExe -or -not (Test-Path $SetupExe)) {
    Write-Error "ERROR: Setup EXE not found. Pass -SetupExe <path>."
    exit 1
}
Write-Host "[E2E] Installer: $SetupExe"
Write-Host "[E2E] Install dir: $TestInstallDir"
Write-Host "[E2E] Port: $Port"

# ── Helper ──────────────────────────────────────────────────────────────
$failures = [System.Collections.Generic.List[string]]::new()
function Fail([string]$msg) { $script:failures.Add($msg); Write-Host "::error:: FAIL: $msg" }
function Pass([string]$msg) { Write-Host "  PASS: $msg" }

# ── Cleanup any leftover from previous run ───────────────────────────────
if (Test-Path $TestInstallDir) {
    Write-Host "[E2E] Removing leftover install dir..."
    Remove-Item $TestInstallDir -Recurse -Force -ErrorAction SilentlyContinue
}

# ══════════════════════════════════════════════════════════════════════════
# STEP 1 — Silent install
# ══════════════════════════════════════════════════════════════════════════
Write-Host "`n[Step 1] Silent install..."
$p = Start-Process -FilePath $SetupExe `
    -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART","/DIR=`"$TestInstallDir`"" `
    -Wait -PassThru
if ($p.ExitCode -ne 0) { Fail "Installer exited with code $($p.ExitCode)"; exit 1 }
Pass "Installer exited 0"

# ══════════════════════════════════════════════════════════════════════════
# STEP 2 — Verify critical files
# ══════════════════════════════════════════════════════════════════════════
Write-Host "`n[Step 2] Verifying installed files..."
$exePath = Join-Path $TestInstallDir "Koto.exe"
$internalDir = Join-Path $TestInstallDir "_internal"

$requiredPaths = @(
    $exePath,
    $internalDir,
    (Join-Path $internalDir "psutil"),
    (Join-Path $internalDir "app"),
    (Join-Path $internalDir "web"),
    (Join-Path $internalDir "config"),
    (Join-Path $TestInstallDir "Start_Koto.bat"),
    (Join-Path $TestInstallDir "unins000.exe")
)
foreach ($path in $requiredPaths) {
    if (Test-Path $path) { Pass "Exists: $(Split-Path -Leaf $path)" }
    else                 { Fail "Missing: $path" }
}

# File size validation — catch empty or corrupt builds
$exeSize = (Get-Item $exePath).Length / 1MB
if ($exeSize -lt 40) { Fail "Koto.exe is only $([math]::Round($exeSize,1))MB (expected >= 40MB)" }
else                  { Pass "Koto.exe size is $([math]::Round($exeSize,1))MB" }

# Start Menu shortcut check
$startMenu = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Koto"
if (Test-Path "$startMenu\Koto.lnk") { Pass "Start Menu shortcut exists" }
else { Write-Host "  WARN: Start Menu shortcut not found (may be optional)" }

# ══════════════════════════════════════════════════════════════════════════
# STEP 3 — Verify registry key (Inno Setup writes under HKCU)
# ══════════════════════════════════════════════════════════════════════════
Write-Host "`n[Step 3] Checking registry..."
$regPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{A3F8E291-7C44-4B2A-9D6E-8C5F1A347B90}_is1"
if (Test-Path $regPath) { Pass "Uninstall registry key exists" }
else                     { Fail "Uninstall registry key missing at $regPath" }

# ══════════════════════════════════════════════════════════════════════════
# STEP 4 — Seed config + launch Koto.exe
# ══════════════════════════════════════════════════════════════════════════
Write-Host "`n[Step 4] Seeding config and launching Koto.exe..."
& (Join-Path $ScriptDir "seed_config.ps1") -InstallDir $TestInstallDir

$env:KOTO_PORT = $Port
if ($env:KOTO_SERVER_ONLY) { Write-Host "  KOTO_SERVER_ONLY=$env:KOTO_SERVER_ONLY (server-only mode)" }
$kotoProc = Start-Process -FilePath $exePath `
    -WorkingDirectory $TestInstallDir `
    -PassThru

Write-Host "  Koto.exe PID: $($kotoProc.Id)"

# ══════════════════════════════════════════════════════════════════════════
# STEP 5 — Poll /api/health
# ══════════════════════════════════════════════════════════════════════════
Write-Host "`n[Step 5] Waiting for http://localhost:$Port/api/health (up to ${HealthTimeoutSec}s)..."
$healthUrl = "http://localhost:$Port/api/health"
$deadline  = (Get-Date).AddSeconds($HealthTimeoutSec)
$healthy   = $false

while ((Get-Date) -lt $deadline) {
    if ($kotoProc.HasExited) {
        Fail "Koto.exe exited unexpectedly (code $($kotoProc.ExitCode)) before health check"
        break
    }
    try {
        $resp = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 3 -ErrorAction Stop
        if ($resp.success -eq $true -or $resp.status -eq "ok" -or $resp.status -eq "healthy") {
            $healthy = $true
            Pass "/api/health returned success"
            break
        }
    } catch {
        # Not up yet — keep polling
    }
    Start-Sleep -Milliseconds 1000
}

if (-not $healthy -and -not $kotoProc.HasExited) {
    # Try raw status code as a fallback (health endpoint may return 200 without 'success' key)
    try {
        $raw = Invoke-WebRequest -Uri $healthUrl -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
        if ($raw.StatusCode -eq 200) {
            $healthy = $true
            Pass "/api/health returned HTTP 200 (raw)"
        }
    } catch {}
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
# STEP 6 — Stop Koto
# ══════════════════════════════════════════════════════════════════════════
Write-Host "`n[Step 6] Stopping Koto.exe..."
if (-not $kotoProc.HasExited) {
    Stop-Process -Id $kotoProc.Id -Force -ErrorAction SilentlyContinue
    $kotoProc.WaitForExit(5000) | Out-Null
    Pass "Process stopped"
}

# ══════════════════════════════════════════════════════════════════════════
# STEP 7 — Silent uninstall
# ══════════════════════════════════════════════════════════════════════════
Write-Host "`n[Step 7] Silent uninstall..."
$uninsExe = Join-Path $TestInstallDir "unins000.exe"
if (Test-Path $uninsExe) {
    $u = Start-Process -FilePath $uninsExe `
        -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART" `
        -Wait -PassThru
    if ($u.ExitCode -eq 0) { Pass "Uninstaller exited 0" }
    else                   { Fail "Uninstaller exited $($u.ExitCode)" }
} else {
    Fail "unins000.exe not found - cannot uninstall"
}

# ══════════════════════════════════════════════════════════════════════════
# STEP 8 — Verify cleanup
# ══════════════════════════════════════════════════════════════════════════
Write-Host "`n[Step 8] Verifying uninstall cleaned up..."
Start-Sleep -Seconds 2
if (Test-Path $exePath) { Fail "Koto.exe still present after uninstall" }
else                    { Pass "Koto.exe removed" }

if (-not (Test-Path $regPath)) { Pass "Registry key removed after uninstall" }
else                           { Fail "Registry key still present after uninstall" }

# ══════════════════════════════════════════════════════════════════════════
# STEP 9 — Reinstall (upgrade scenario test)
# ══════════════════════════════════════════════════════════════════════════
Write-Host "`n[Step 9] Reinstalling (upgrade scenario)..."
$p2 = Start-Process -FilePath $SetupExe `
    -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART","/DIR=`"$TestInstallDir`"" `
    -Wait -PassThru
if ($p2.ExitCode -ne 0) { Fail "Reinstall exited with code $($p2.ExitCode)" }
else                     { Pass "Reinstall exited 0" }

# Verify files are present again
if (Test-Path $exePath) { Pass "Koto.exe present after reinstall" }
else                    { Fail "Koto.exe missing after reinstall" }
if (Test-Path (Join-Path $TestInstallDir "unins000.exe")) { Pass "unins000.exe present after reinstall" }
else                                                       { Fail "unins000.exe missing after reinstall" }

# ══════════════════════════════════════════════════════════════════════════
# STEP 10 — Second uninstall + verify cleanup
# ══════════════════════════════════════════════════════════════════════════
Write-Host "`n[Step 10] Second silent uninstall..."
$uninsExe2 = Join-Path $TestInstallDir "unins000.exe"
if (Test-Path $uninsExe2) {
    $u2 = Start-Process -FilePath $uninsExe2 `
        -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART" `
        -Wait -PassThru
    if ($u2.ExitCode -eq 0) { Pass "Second uninstaller exited 0" }
    else                     { Fail "Second uninstaller exited $($u2.ExitCode)" }
} else {
    Fail "unins000.exe not found for second uninstall"
}

Start-Sleep -Seconds 2
if (Test-Path $exePath) { Fail "Koto.exe still present after second uninstall" }
else                    { Pass "Koto.exe removed after second uninstall" }

# ══════════════════════════════════════════════════════════════════════════
# RESULT
# ══════════════════════════════════════════════════════════════════════════
Write-Host ""
if ($failures.Count -eq 0) {
    Write-Host "✅ Installer E2E: ALL CHECKS PASSED" -ForegroundColor Green
    exit 0
} else {
    Write-Host "❌ Installer E2E: $($failures.Count) CHECK(S) FAILED:" -ForegroundColor Red
    $failures | ForEach-Object { Write-Host "   - $_" -ForegroundColor Red }
    exit 1
}
