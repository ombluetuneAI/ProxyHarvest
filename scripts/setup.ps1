# ProxyHarvest Windows Setup Script
# Downloads subconverter and singtools binaries, installs Python dependencies.
# Run from the project root: powershell -ExecutionPolicy Bypass -File scripts/setup.ps1

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$null = python --version 2>&1
if ($LASTEXITCODE -ne 0) { Write-Error "Python not found. Please install Python 3.10+ and add it to PATH."; exit 1 }

Write-Host "=== ProxyHarvest Setup ===" -ForegroundColor Cyan
Write-Host ""

# ── Versions ────────────────────────────────────────────────────
$SubconverterVersion = "v0.9.2"
$SubconverterRepo    = "MetaCubeX/subconverter"
$SubconverterFile    = "subconverter_win64.7z"
$SingtoolsVersion    = "vv0.2.0"
$SingtoolsFile       = "singtools_win64.7z"

# ── Python dependencies ─────────────────────────────────────────
Write-Host "[1/3] Installing Python dependencies..." -ForegroundColor Yellow
pip install -r "$ProjectRoot\requirements.txt" --quiet 2>&1 | Out-Null
pip install geoip2 py7zr --quiet 2>&1 | Out-Null
Write-Host "       Done." -ForegroundColor Green

# ── Download & extract subconverter ──────────────────────────────
Write-Host "[2/3] Setting up subconverter $SubconverterVersion..." -ForegroundColor Yellow

$SubDir = "$ProjectRoot\tools\subconverter\windows"
$SubUrl = "https://github.com/$SubconverterRepo/releases/download/$SubconverterVersion/$SubconverterFile"
$SubZip = "$env:TEMP\$SubconverterFile"

New-Item -ItemType Directory -Force -Path $SubDir | Out-Null

Write-Host "       Downloading subconverter..." -ForegroundColor Gray
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Invoke-WebRequest -Uri $SubUrl -OutFile $SubZip -UseBasicParsing

Write-Host "       Extracting..." -ForegroundColor Gray
python -c @"
import py7zr, os, shutil, sys
zip_path = sys.argv[1]
out_dir = sys.argv[2]
with py7zr.SevenZipFile(zip_path, 'r') as z:
    z.extractall(out_dir)
# Move files up if nested in subconverter/ directory
sub_dir = os.path.join(out_dir, 'subconverter')
if os.path.isdir(sub_dir):
    for item in os.listdir(sub_dir):
        src = os.path.join(sub_dir, item)
        dst = os.path.join(out_dir, item)
        if os.path.exists(dst):
            if os.path.isdir(dst): shutil.rmtree(dst)
            else: os.remove(dst)
        shutil.move(src, dst)
    os.rmdir(sub_dir)
print(f'Extracted to {out_dir}')
"@ "$SubZip" "$SubDir"

Remove-Item $SubZip -Force -ErrorAction SilentlyContinue
Write-Host "       subconverter ready: $SubDir\subconverter.exe" -ForegroundColor Green

# ── Download & extract singtools ─────────────────────────────────
Write-Host "[3/3] Setting up singtools $SingtoolsVersion..." -ForegroundColor Yellow

$SingDir = "$ProjectRoot\tools\singtools\windows"
$SingUrl = "https://github.com/Kdwkakcs/singtools/releases/download/$SingtoolsVersion/$SingtoolsFile"
$SingZip = "$env:TEMP\$SingtoolsFile"

New-Item -ItemType Directory -Force -Path $SingDir | Out-Null

Write-Host "       Downloading singtools..." -ForegroundColor Gray
Invoke-WebRequest -Uri $SingUrl -OutFile $SingZip -UseBasicParsing

Write-Host "       Extracting..." -ForegroundColor Gray
python -c @"
import py7zr, os, sys
zip_path = sys.argv[1]
out_dir = sys.argv[2]
with py7zr.SevenZipFile(zip_path, 'r') as z:
    z.extractall(out_dir)
print(f'Extracted to {out_dir}')
"@ "$SingZip" "$SingDir"

Remove-Item $SingZip -Force -ErrorAction SilentlyContinue
Write-Host "       singtools ready: $SingDir\singtools.exe" -ForegroundColor Green

Write-Host ""
Write-Host "=== Setup complete! ===" -ForegroundColor Cyan
Write-Host "Run:  python scripts\run.py all" -ForegroundColor White
