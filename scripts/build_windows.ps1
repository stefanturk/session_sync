# Build SessionSync for Windows: dist\SessionSync\SessionSync.exe, a zip of it,
# and (when Inno Setup is installed) dist\SessionSync-Setup-<version>.exe.
#
#   powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
#
# Needs Python 3.12+ on PATH (or $env:PYTHON). Run on Windows; PyInstaller
# cannot build a Windows program on a Mac.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = if ($env:PYTHON) { $env:PYTHON } else { "python" }
$version = "0.2.0"

& $python -m pip install --quiet --upgrade pip
& $python -m pip install --quiet -e ".[package]" pillow
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# --- the icon --------------------------------------------------------------
$work = Join-Path $env:TEMP ("sessionsync-build-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $work | Out-Null
$png = Join-Path $work "SessionSync.png"
& $python scripts\render_icon.py $png 256
if ($LASTEXITCODE -eq 0 -and (Test-Path $png)) { $env:SESSIONSYNC_ICO = $png }
else { Write-Warning "No icon rendered; using the generic one." }

# --- the program -----------------------------------------------------------
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
& $python -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\SessionSync.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
if (-not (Test-Path dist\SessionSync\SessionSync.exe)) { throw "Build produced no SessionSync.exe" }

Compress-Archive -Path dist\SessionSync -DestinationPath "dist\SessionSync-$version-windows.zip" -Force

# --- the installer ---------------------------------------------------------
$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($iscc) {
    & $iscc "/DAppVersion=$version" packaging\SessionSync.iss
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
} else {
    Write-Warning "Inno Setup 6 not found - built the zip only (choco install innosetup)."
}

Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
Get-ChildItem dist | Format-Table Name, Length
