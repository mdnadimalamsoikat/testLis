# install_service_windows.ps1
# ---------------------------------------------------------------------------
# Install the LIS middleware as a Windows service, driven by APEX.
# Run PowerShell **as Administrator**:
#
#   .\ops\install_service_windows.ps1 -ProjectDir "C:\lis\lis-middleware"
#   .\ops\install_service_windows.ps1 -ProjectDir "C:\lis\lis-middleware" -Uninstall
#
# What it does:
#   * finds Python (or uses -PythonExe)
#   * stores the DB password in the machine environment (never in a file)
#   * registers a service that runs `python service.py` (APEX-driven mode)
#   * restarts it automatically if it ever stops
# ---------------------------------------------------------------------------
param(
    [string]$ProjectDir = "C:\lis\lis-middleware",
    [string]$ServiceName = "LISMiddleware",
    [string]$PythonExe = "",
    [string]$DbPassword = "",
    [string]$NssmPath = "",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"

function Find-Nssm {
    if ($NssmPath -and (Test-Path $NssmPath)) { return $NssmPath }
    $cmd = Get-Command nssm.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    # download a local copy next to the script
    $target = Join-Path $PSScriptRoot "nssm.exe"
    if (Test-Path $target) { return $target }
    Write-Host "nssm.exe not found — downloading..." -ForegroundColor Yellow
    $zip = Join-Path $env:TEMP "nssm.zip"
    Invoke-WebRequest -Uri "https://nssm.cc/release/nssm-2.24.zip" -OutFile $zip
    Expand-Archive $zip -DestinationPath (Join-Path $env:TEMP "nssm") -Force
    Copy-Item (Join-Path $env:TEMP "nssm\nssm-2.24\win64\nssm.exe") $target
    return $target
}

function Find-Python {
    if ($PythonExe) { return $PythonExe }
    foreach ($candidate in @("python.exe", "py.exe")) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    throw "Python not found — install it or pass -PythonExe"
}

$nssm = Find-Nssm

if ($Uninstall) {
    & $nssm stop $ServiceName 2>$null
    & $nssm remove $ServiceName confirm
    Write-Host "service '$ServiceName' removed" -ForegroundColor Yellow
    return
}

$python = Find-Python
if (-not (Test-Path (Join-Path $ProjectDir "service.py"))) {
    throw "service.py not found in $ProjectDir — fix -ProjectDir"
}

if ($DbPassword) {
    [Environment]::SetEnvironmentVariable("LIS_DB_PASSWORD", $DbPassword, "Machine")
    Write-Host "LIS_DB_PASSWORD stored in the machine environment" -ForegroundColor Green
}

Write-Host "installing service '$ServiceName'"
& $nssm install $ServiceName $python "`"$ProjectDir\service.py`""
& $nssm set $ServiceName AppDirectory $ProjectDir
& $nssm set $ServiceName DisplayName "LIS Middleware (APEX)"
& $nssm set $ServiceName Description "ASTM/HL7 analyzer middleware — configured from Oracle APEX"
& $nssm set $ServiceName Start SERVICE_AUTO_START
& $nssm set $ServiceName AppStdout (Join-Path $ProjectDir "logs\stdout.log")
& $nssm set $ServiceName AppStderr (Join-Path $ProjectDir "logs\stderr.log")
& $nssm set $ServiceName AppRotateFiles 1
& $nssm set $ServiceName AppRotateBytes 10485760
& $nssm set $ServiceName AppExit Default Restart
& $nssm set $ServiceName AppRestartDelay 5000
& $nssm set $ServiceName AppEnvironmentExtra "LIS_CONFIG_MODE=db"

& $nssm start $ServiceName
Start-Sleep -Seconds 3
& $nssm status $ServiceName

Write-Host ""
Write-Host "done. useful commands:" -ForegroundColor Cyan
Write-Host "  nssm status  $ServiceName"
Write-Host "  nssm restart $ServiceName"
Write-Host "  Get-Content `"$ProjectDir\logs\lis_service.log`" -Wait"
Write-Host ""
Write-Host "The service reads its configuration from Oracle (lis_config)." -ForegroundColor Cyan
Write-Host "Change an analyzer in APEX → it applies within ~5 seconds. No restart."
