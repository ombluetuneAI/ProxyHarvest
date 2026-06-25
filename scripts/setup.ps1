# ProxyHarvest Windows Setup Script
# Downloads subconverter, singtools and mihomo binaries, installs Python dependencies.
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
$MihomoVersion       = "v1.19.27"
$MihomoRepo          = "MetaCubeX/mihomo"
$MihomoFile          = "mihomo-windows-amd64-compatible-$MihomoVersion.zip"

# ── Python dependencies ─────────────────────────────────────────
Write-Host "[1/4] Installing Python dependencies..." -ForegroundColor Yellow
pip install -r "$ProjectRoot\requirements.txt" --quiet 2>&1 | Out-Null
pip install geoip2 py7zr --quiet 2>&1 | Out-Null
Write-Host "       Done." -ForegroundColor Green

# ── Download & extract subconverter ──────────────────────────────
Write-Host "[2/4] Setting up subconverter $SubconverterVersion..." -ForegroundColor Yellow

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
Write-Host "[3/4] Setting up singtools $SingtoolsVersion..." -ForegroundColor Yellow

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

# ── Download & extract mihomo (standalone validation core) ───────
Write-Host "[4/4] Setting up mihomo $MihomoVersion..." -ForegroundColor Yellow

$MihomoDir = "$ProjectRoot\tools\mihomo\windows"
$MihomoUrl = "https://github.com/$MihomoRepo/releases/download/$MihomoVersion/$MihomoFile"
$MihomoZip = "$env:TEMP\$MihomoFile"

New-Item -ItemType Directory -Force -Path $MihomoDir | Out-Null

Write-Host "       Downloading mihomo..." -ForegroundColor Gray
Invoke-WebRequest -Uri $MihomoUrl -OutFile $MihomoZip -UseBasicParsing

Write-Host "       Extracting..." -ForegroundColor Gray
# The zip holds a single exe named mihomo-windows-amd64-compatible-<ver>.exe;
# extract it and normalise the name to mihomo.exe.
Expand-Archive -Path $MihomoZip -DestinationPath $MihomoDir -Force
$MihomoExe = Get-ChildItem -Path $MihomoDir -Filter "mihomo*.exe" | Select-Object -First 1
if ($null -eq $MihomoExe) { Write-Error "mihomo executable not found after extraction"; exit 1 }
if ($MihomoExe.Name -ne "mihomo.exe") {
    Move-Item -Path $MihomoExe.FullName -Destination "$MihomoDir\mihomo.exe" -Force
}

Remove-Item $MihomoZip -Force -ErrorAction SilentlyContinue
Write-Host "       mihomo ready: $MihomoDir\mihomo.exe" -ForegroundColor Green

Write-Host ""
Write-Host "=== Setup complete! ===" -ForegroundColor Cyan
Write-Host "Run:  python scripts\run.py all" -ForegroundColor White
