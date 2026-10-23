<#
.SYNOPSIS
    Builds the self-contained installer dist\ShapeShifter-Setup-<version>.exe.

.DESCRIPTION
    1. Creates a clean build environment (.build\venv) with a standard CPython 3.10+.
    2. Freezes ShapeShifter with PyInstaller into .build\dist\ShapeShifter\ (no Python needed).
    3. Bundles ffmpeg.exe (found on this PC, or given with -FFmpeg) unless -NoFFmpeg.
    4. Wraps everything into a one-file setup program.

.PARAMETER NoFFmpeg
    Don't bundle ffmpeg (a much smaller installer; setup offers to install it with winget).
.PARAMETER FFmpeg
    Path to the ffmpeg.exe to bundle (default: the one on PATH).
.PARAMETER SkipTests
    Skip running the test suite before building.
.PARAMETER NoInno
    Skip Inno Setup and build the older self-extracting setup instead.
.PARAMETER Sign
    Code-sign the app and installer with signtool (needs -SignCommand or env SHAPESHIFTER_SIGN,
    e.g. 'signtool sign /fd sha256 /tr http://timestamp.digicert.com /td sha256 /a $f').
    Untested: no certificate was available when this was written.
#>
param([switch]$NoFFmpeg, [string]$FFmpeg, [switch]$SkipTests, [switch]$NoInno, [string]$PythonExe,
      [switch]$Sign, [string]$SignCommand = $env:SHAPESHIFTER_SIGN)

$ErrorActionPreference = 'Continue'
$Root = $PSScriptRoot
$Build = Join-Path $Root '.build'
$Dist = Join-Path $Root 'dist'
function Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host $m -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------- Python
Step 'Finding a standard Python (not Anaconda / Store)'
$check = "import os,sys; sys.exit(0 if sys.version_info >= (3,10) and not os.path.exists(os.path.join(sys.base_prefix,'conda-meta')) else 1)"
$Python = $null
if ($PythonExe) { $Python = $PythonExe }       # CI passes its own interpreter
$candidates = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
    $candidates += (& py -0p 2>$null) | ForEach-Object { if ($_ -match '([A-Za-z]:\\.+python\.exe)') { $Matches[1].Trim() } }
}
$candidates += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue | ForEach-Object FullName
foreach ($c in $(if ($PythonExe) { @() } else { $candidates | Select-Object -Unique })) {
    if ($c -match 'WindowsApps') { continue }
    & $c -c $check 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $Python = $c; break }
}
if (-not $Python) { Fail 'No suitable Python 3.10+ found. Install one from python.org.' }
Write-Host "    $Python"

# ----------------------------------------------------------- environment
Step 'Preparing the build environment'
$Venv = Join-Path $Build 'venv'
if (-not (Test-Path $Venv)) { & $Python -m venv $Venv }
$VPy = Join-Path $Venv 'Scripts\python.exe'
& $VPy -m pip install --quiet --disable-pip-version-check --upgrade pip
& $VPy -m pip install --quiet --disable-pip-version-check --upgrade "$Root" pyinstaller pytest
if ($LASTEXITCODE) { Fail 'pip install failed' }
$Version = (& $VPy -c "import shapeshifter; print(shapeshifter.__version__)").Trim()
Write-Host "    ShapeShifter $Version"

if (-not $SkipTests) {
    Step 'Running tests'
    Push-Location $Root
    & $VPy -m pytest -q tests
    $code = $LASTEXITCODE
    Pop-Location
    if ($code) { Fail 'Tests failed; not building.' }
}

# ------------------------------------------------------------------ freeze
Step 'Freezing ShapeShifter.exe with PyInstaller'
$Icon = Join-Path $Build 'ShapeShifter.ico'
& $VPy -c "import shutil; from shapeshifter.icon import ensure_icon; shutil.copy(ensure_icon(), r'$Icon')"
$VerInfo = Join-Path $Build 'version_info.txt'
& $VPy (Join-Path $Root 'packaging\make_version_info.py') $VerInfo 'ShapeShifter - convert files in place' 'ShapeShifter.exe'
function Sign-File($f) {
    if (-not $Sign) { return }
    if (-not $SignCommand) { Fail '-Sign needs -SignCommand (or env SHAPESHIFTER_SIGN).' }
    Invoke-Expression ($SignCommand -replace '\$f', "'$f'")
    if ($LASTEXITCODE) { Fail "Signing failed: $f" }
}
$work = Join-Path $Build 'work'
$appDist = Join-Path $Build 'dist'
Remove-Item -Recurse -Force $appDist -ErrorAction SilentlyContinue
& $VPy -m PyInstaller --noconfirm --clean --windowed --name ShapeShifter --icon $Icon --version-file $VerInfo `
    --distpath $appDist --workpath $work --specpath $Build `
    --collect-submodules shapeshifter --collect-all pymupdf --collect-all pillow_heif `
    --collect-all py7zr --collect-submodules comtypes `
    --hidden-import win32com.propsys --hidden-import win32com.shell.shell `
    --exclude-module tkinter --exclude-module pytest `
    --exclude-module pymupdf.layout --exclude-module onnxruntime --exclude-module numpy `
    (Join-Path $Root 'packaging\launcher.py')
if ($LASTEXITCODE) { Fail 'PyInstaller failed' }
$App = Join-Path $appDist 'ShapeShifter'

if (-not $NoFFmpeg) {
    if (-not $FFmpeg) { $FFmpeg = (Get-Command ffmpeg -ErrorAction SilentlyContinue).Source }
    if ($FFmpeg -and (Test-Path $FFmpeg)) {
        Step "Bundling ffmpeg ($FFmpeg)"
        New-Item -ItemType Directory -Force (Join-Path $App 'ffmpeg') | Out-Null
        Copy-Item $FFmpeg (Join-Path $App 'ffmpeg\ffmpeg.exe')
        $pkg = Split-Path (Split-Path $FFmpeg)
        $ffLic = Join-Path $pkg 'LICENSE'
        if (Test-Path $ffLic) { Copy-Item $ffLic (Join-Path $App 'ffmpeg\LICENSE.txt') }
        Set-Content -Path (Join-Path $App 'ffmpeg\SOURCE.txt') -Encoding utf8 -Value @(
            'This folder contains an unmodified FFmpeg build (GPL v3, built with --enable-gpl --enable-version3).',
            'FFmpeg is a separate program that ShapeShifter runs; it is not part of ShapeShifter itself.',
            'Source code: https://ffmpeg.org/download.html#get-sources',
            'Build used: https://www.gyan.dev/ffmpeg/builds/  (the matching source is linked from that page)',
            'You may replace ffmpeg.exe with any other ffmpeg build.')
    } else {
        Write-Host '    ffmpeg not found - building without it (setup will offer winget).' -ForegroundColor Yellow
    }
}
Set-Content -Path (Join-Path $App 'version.txt') -Value $Version -Encoding ascii

Step 'Collecting third-party licences'
$Lic = Join-Path $Build 'licenses'
$licArgs = @('--exe', (Join-Path $App 'ShapeShifter.exe'), '--out', $Lic,
             '--md', (Join-Path $Root 'THIRD-PARTY-NOTICES.md'), '--version', $Version)
if (Test-Path (Join-Path $App 'ffmpeg\ffmpeg.exe')) { $licArgs += @('--ffmpeg-dir', (Join-Path $App 'ffmpeg')) }
& $VPy (Join-Path $Root 'packaging\collect_licenses.py') @licArgs
if ($LASTEXITCODE) { Fail 'Collecting licences failed' }
Sign-File (Join-Path $App 'ShapeShifter.exe')

Step 'Checking the frozen app (selftest)'
& (Join-Path $App 'ShapeShifter.exe') selftest | Out-Host
if ($LASTEXITCODE) { Fail 'The frozen app failed its self test.' }

# ------------------------------------------------------------------ setup
New-Item -ItemType Directory -Force $Dist | Out-Null
$ISCC = $null
if (-not $NoInno) {
    $ISCC = (Get-Command ISCC -ErrorAction SilentlyContinue).Source
    if (-not $ISCC) {
        $ISCC = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
                  "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
    }
    if (-not $ISCC) { Write-Host '    Inno Setup not found - using the self-extracting setup.' -ForegroundColor Yellow }
}
$out = $null
if ($ISCC) {
    Step 'Building the installer with Inno Setup'
    $isArgs = @("/DAppVersion=$Version", "/DSourceDir=$App", "/DLicensesDir=$Lic", "/DRepoRoot=$Root",
                "/DIconFile=$Icon", "/DOutputDir=$Dist")
    & $ISCC @isArgs (Join-Path $Root 'packaging\installer.iss')
    if ($LASTEXITCODE) { Fail 'Inno Setup failed' }
    $out = Join-Path $Dist "ShapeShifter-Setup-$Version.exe"
}
else {
Step 'Packing the installer'
$payload = Join-Path $Build 'payload.zip'
& $VPy -c "import zipfile, pathlib; src = pathlib.Path(r'$App'); z = zipfile.ZipFile(r'$payload', 'w', zipfile.ZIP_STORED); [z.write(p, p.relative_to(src).as_posix()) for p in sorted(src.rglob('*')) if p.is_file()]; z.close()"
New-Item -ItemType Directory -Force $Dist | Out-Null
$setupName = "ShapeShifter-Setup-$Version"
& $VPy -m PyInstaller --noconfirm --onefile --windowed --name $setupName --icon $Icon `
    --distpath $Dist --workpath (Join-Path $Build 'work-setup') --specpath $Build `
    --add-data "$payload;." --add-data "$Icon;." `
    (Join-Path $Root 'packaging\setup_main.py')
if ($LASTEXITCODE) { Fail 'Building the setup program failed' }
$out = Join-Path $Dist "$setupName.exe"
}
Sign-File $out

# ------------------------------------------------------- release extras
Step 'Checksums and source archive'
$srcZip = Join-Path $Dist "shapeshifter-$Version-source.zip"
& $VPy -I (Join-Path $Root 'packaging\make_source_zip.py') $Root $srcZip $Version
$sums = foreach ($f in @($out, $srcZip)) { "{0}  {1}" -f (Get-FileHash $f -Algorithm SHA256).Hash.ToLower(), (Split-Path $f -Leaf) }
Set-Content -Path (Join-Path $Dist 'SHA256SUMS.txt') -Value $sums -Encoding ascii
Copy-Item $out (Join-Path $Dist 'ShapeShifter-Setup.exe') -Force

$mb = [math]::Round((Get-Item $out).Length / 1MB, 1)
Write-Host ''
Write-Host "Built $out ($mb MB)" -ForegroundColor Green
$sums | ForEach-Object { Write-Host "    $_" }
Write-Host 'Run it to install, or pass /S (self-extracting) or /VERYSILENT (Inno) for a silent install.'
