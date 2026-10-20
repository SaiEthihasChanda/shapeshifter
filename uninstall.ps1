<#
.SYNOPSIS
    Removes ShapeShifter for the current user.
.PARAMETER KeepSettings
    Keep %APPDATA%\ShapeShifter\config.json.
#>
param([switch]$KeepSettings)

$ErrorActionPreference = 'Continue'
$Root = Join-Path $env:LOCALAPPDATA 'ShapeShifter'
$VPy = Join-Path $Root 'venv\Scripts\python.exe'

Write-Host '==> Stopping ShapeShifter' -ForegroundColor Cyan
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'shapeshifter' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

Write-Host '==> Removing Windows integration' -ForegroundColor Cyan
if (Test-Path $VPy) {
    & $VPy -m shapeshifter uninstall
} else {
    # Fallback if the environment is already gone.
    Remove-Item -LiteralPath 'HKCU:\Software\Classes\*\shell\ShapeShifter' -Recurse -Force -ErrorAction SilentlyContinue
    $sfa = 'HKCU:\Software\Classes\SystemFileAssociations'
    Get-ChildItem $sfa -ErrorAction SilentlyContinue | ForEach-Object {
        $k = Join-Path $_.PSPath 'shell\ShapeShifter'
        if (Test-Path $k) { Remove-Item $k -Recurse -Force }
    }
    Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ShapeShifter' -Recurse -Force -ErrorAction SilentlyContinue
    $programs = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
    Remove-Item (Join-Path $programs 'ShapeShifter.lnk'), (Join-Path $programs 'Startup\ShapeShifter.lnk') -Force -ErrorAction SilentlyContinue
}

Write-Host '==> Deleting files' -ForegroundColor Cyan
Start-Sleep -Milliseconds 500
Remove-Item $Root -Recurse -Force -ErrorAction SilentlyContinue
if (-not $KeepSettings) {
    Remove-Item (Join-Path $env:APPDATA 'ShapeShifter') -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host 'ShapeShifter has been uninstalled.' -ForegroundColor Green
