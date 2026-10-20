<#
.SYNOPSIS
    Installs ShapeShifter for the current user (no admin rights needed).

.DESCRIPTION
    - Finds a standard CPython >= 3.10 (python.org / winget build; Anaconda and Microsoft
      Store builds are skipped because they break Qt or virtualise the registry).
    - Creates a private virtual environment in %LOCALAPPDATA%\ShapeShifter\venv.
    - Registers the "Convert to..." context-menu entry, Start Menu + startup shortcuts,
      and an entry in Settings > Apps.
    - Starts the tray app (global hotkey Ctrl+Alt+C).

.PARAMETER NoStartup
    Don't start the tray app automatically when Windows starts.

.PARAMETER Yes
    Answer "yes" to prompts (installing Python/ffmpeg with winget if missing).
#>
param([switch]$NoStartup, [switch]$Yes)

# 'Continue' so stderr from native tools (pip warnings) isn't fatal in Windows PowerShell;
# failures are detected through $LASTEXITCODE instead.
$ErrorActionPreference = 'Continue'
$Root = Join-Path $env:LOCALAPPDATA 'ShapeShifter'
$Venv = Join-Path $Root 'venv'
$Src = $PSScriptRoot

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }

function Confirm-Action($question) {
    if ($Yes) { return $true }
    $answer = Read-Host "$question [Y/n]"
    return ($answer -eq '' -or $answer -match '^[Yy]')
}

function Test-Python([string]$exe) {
    if (-not $exe -or -not (Test-Path $exe)) { return $false }
    if ($exe -match 'WindowsApps') { return $false }
    # Single line, no double quotes: Windows PowerShell 5.1 mangles quotes in native args.
    $check = "import os,sys; sys.exit(0 if sys.version_info >= (3,10) and not os.path.exists(os.path.join(sys.base_prefix,'conda-meta')) else 1)"
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    try { & $exe -c $check 2>$null | Out-Null } finally { $ErrorActionPreference = $prev }
    return $LASTEXITCODE -eq 0
}

function Find-Python {
    $candidates = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $candidates += (& py -0p 2>$null) | ForEach-Object {
            if ($_ -match '([A-Za-z]:\\.+python\.exe)') { $Matches[1].Trim() }
        }
    }
    $candidates += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe",
                                 "$env:ProgramFiles\Python3*\python.exe" -ErrorAction SilentlyContinue |
                   Sort-Object FullName -Descending | ForEach-Object FullName
    $candidates += (Get-Command python -All -ErrorAction SilentlyContinue).Source
    foreach ($c in ($candidates | Where-Object { $_ } | Select-Object -Unique)) {
        if (Test-Python $c) { return $c }
    }
    return $null
}

# ------------------------------------------------------------------ Python
Write-Step 'Looking for a suitable Python'
$Python = Find-Python
if (-not $Python) {
    Write-Host 'No standard Python 3.10+ found (Anaconda/Store builds are not supported).' -ForegroundColor Yellow
    if ((Get-Command winget -ErrorAction SilentlyContinue) -and (Confirm-Action 'Install Python 3.12 with winget (per-user)?')) {
        winget install --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
        $Python = Find-Python
    }
    if (-not $Python) {
        throw 'Please install Python 3.10+ from https://www.python.org/downloads/ and run this installer again.'
    }
}
Write-Host "    using $Python"

# ------------------------------------------------- stop a running instance
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'shapeshifter' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# --------------------------------------------------------------- venv + pip
Write-Step "Creating environment in $Venv"
New-Item -ItemType Directory -Force $Root | Out-Null
if (Test-Path $Venv) {
    $cfg = Get-Content (Join-Path $Venv 'pyvenv.cfg') -ErrorAction SilentlyContinue | Out-String
    if ($cfg -notmatch [regex]::Escape((Split-Path $Python))) { Remove-Item -Recurse -Force $Venv }
}
if (-not (Test-Path $Venv)) { & $Python -m venv $Venv; if ($LASTEXITCODE) { throw 'venv creation failed' } }
$VPy = Join-Path $Venv 'Scripts\python.exe'

Write-Step 'Installing ShapeShifter and its dependencies (this can take a minute)'
& $VPy -m pip install --quiet --disable-pip-version-check --upgrade pip
& $VPy -m pip install --quiet --disable-pip-version-check --upgrade "$Src"
if ($LASTEXITCODE) { throw 'pip install failed' }

& $VPy -c "import PySide6.QtWidgets, PIL, pymupdf" 2>$null
if ($LASTEXITCODE) { throw 'The installed packages failed to load. See the output above.' }

# ------------------------------------------------------------------ ffmpeg
$ff = & $VPy -c "from shapeshifter.backends.media import ffmpeg_path; print(ffmpeg_path() or '')"
if (-not $ff) {
    Write-Host 'ffmpeg was not found - audio/video conversion will be unavailable.' -ForegroundColor Yellow
    if ((Get-Command winget -ErrorAction SilentlyContinue) -and (Confirm-Action 'Install ffmpeg with winget?')) {
        winget install --id Gyan.FFmpeg --silent --accept-package-agreements --accept-source-agreements
        Write-Host '    ffmpeg installed. Run "Supported formats" from the tray menu to confirm.'
    }
} else {
    Write-Host "    ffmpeg: $ff"
}

# ------------------------------------------------------- shell integration
Write-Step 'Registering with Windows'
Copy-Item (Join-Path $Src 'uninstall.ps1') (Join-Path $Root 'uninstall.ps1') -Force
$UninstallCmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$Root\uninstall.ps1`""
$installArgs = @('-m', 'shapeshifter', 'install', '--uninstall-cmd', $UninstallCmd, '--install-dir', $Root)
if ($NoStartup) { $installArgs += '--no-startup' }
& $VPy @installArgs
if ($LASTEXITCODE) { throw 'Shell registration failed' }

# ------------------------------------------------------------------- start
Write-Step 'Starting the tray app'
Start-Process (Join-Path $Venv 'Scripts\pythonw.exe') -ArgumentList '-m', 'shapeshifter', 'daemon'

Write-Host ''
Write-Host 'ShapeShifter is installed.' -ForegroundColor Green
Write-Host '  * Select file(s) in File Explorer and press Ctrl+Alt+C, or'
Write-Host '  * Right-click a file > "Convert to..." (on Windows 11: Show more options, or Shift+right-click)'
Write-Host '  Move the mouse onto a format and click. Esc cancels.'
