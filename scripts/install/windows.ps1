# Interactive installer for Koetai (local, single-user) on Windows.
# Automates the docker-compose path from the README's Setup section — nothing
# here is Koetai-internal magic. Safe to re-run: it pulls instead of
# re-cloning, and reuses an existing .env.
#
# Run from PowerShell (Docker Desktop's WSL2 backend is required):
#   powershell -ExecutionPolicy Bypass -File windows.ps1

$ErrorActionPreference = "Stop"

function Say($msg)  { Write-Host ""; Write-Host $msg -ForegroundColor Cyan }
function Ask($prompt, $default) {
    $ans = Read-Host "$prompt [$default]"
    if ([string]::IsNullOrWhiteSpace($ans)) { return $default }
    return $ans
}
function YesNo($prompt, $default = "y") {
    $ans = Read-Host "$prompt [y/n] (default $default)"
    if ([string]::IsNullOrWhiteSpace($ans)) { $ans = $default }
    return $ans -match '^[Yy]'
}
function PortInUse($port) {
    return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

Say "Koetai local install (Windows)"
Write-Host "Single-user instance in Docker - no ORCID account, no cloud sign-in,"
Write-Host "your data stays on this machine."

# -- Prerequisites -------------------------------------------------------------
Say "1/6 Checking prerequisites"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "git not found. Install it: https://git-scm.com/download/win"
    exit 1
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "Docker not found. Install Docker Desktop: https://www.docker.com/products/docker-desktop/"
    Write-Host "(Docker Desktop on Windows needs WSL2 - the installer sets that up if it's missing.)"
    exit 1
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker Desktop isn't running. Starting it..."
    $dockerExe = "$Env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    if (Test-Path $dockerExe) { Start-Process $dockerExe }
    Write-Host -NoNewline "Waiting for Docker to come up"
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        docker info *> $null
        if ($LASTEXITCODE -eq 0) { $ready = $true; Write-Host " ready."; break }
        Write-Host -NoNewline "."; Start-Sleep -Seconds 2
    }
    if (-not $ready) {
        Write-Host ""
        Write-Host "Docker Desktop didn't start in time - start it manually and re-run this script."
        exit 1
    }
}
Write-Host "git and Docker OK."

# -- Choices --------------------------------------------------------------------
Say "2/6 A few questions"

$Dir = Ask "Install into which directory?" "$Env:USERPROFILE\koetai-platform"

$RepoChoice = Ask "Clone from (github/codeberg)?" "github"
if ($RepoChoice -eq "codeberg") {
    $RepoUrl = "https://codeberg.org/andrawaag/koetai-platform.git"
} else {
    $RepoUrl = "https://github.com/Koetai/koetai-platform.git"
}

Write-Host ""
Write-Host "Which triplestore should back your datasets?"
Write-Host "  1) Oxigraph  - recommended: small, fast, no extra config"
Write-Host "  2) Fuseki    - heavier, the other tested option"
Write-Host "  3) Both"
$StoreChoice = Ask "Choice" "1"
switch ($StoreChoice) {
    "2" { $Stores = @("fuseki");           $ComposeProfile = @() }
    "3" { $Stores = @("fuseki","oxigraph"); $ComposeProfile = @("--profile","oxigraph") }
    default { $Stores = @("oxigraph");      $ComposeProfile = @("--profile","oxigraph") }
}

$Port = Ask "Port for Koetai" "3002"
while (PortInUse $Port) {
    Write-Host "Port $Port is already in use."
    $Port = Ask "Try a different port" "3003"
}

# -- Clone ------------------------------------------------------------------------
Say "3/6 Getting the code"
if (Test-Path "$Dir\.git") {
    Write-Host "$Dir already exists - pulling latest instead of cloning."
    git -C $Dir pull --ff-only
} else {
    git clone $RepoUrl $Dir
}
Set-Location $Dir

# -- Configure ----------------------------------------------------------------
Say "4/6 Configuring"
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env" }
# KOETAI_MODE is hardcoded to `local` in docker-compose.yml for this path, so
# nothing to set there. Only the port needs to go in .env - compose reads
# ${KOETAI_PORT:-3002} from it automatically.
$envLines = Get-Content ".env"
if ($envLines -match '^KOETAI_PORT=') {
    $envLines = $envLines -replace '^KOETAI_PORT=.*', "KOETAI_PORT=$Port"
    Set-Content ".env" $envLines
} else {
    Add-Content ".env" "KOETAI_PORT=$Port"
}
Write-Host "Wrote $Dir\.env (port=$Port)."

# -- Start ----------------------------------------------------------------------
Say "5/6 Starting containers ($($Stores -join ' ')) - first run also builds the image, this can take a few minutes"
$upArgs = @("compose") + $ComposeProfile + @("up","-d","--build","koetai") + $Stores
docker @upArgs

Write-Host -NoNewline "Waiting for Koetai to answer on port $Port"
$up = $false
for ($i = 0; $i -lt 60; $i++) {
    try {
        $resp = Invoke-WebRequest -UseBasicParsing -Uri "http://localhost:$Port/" -TimeoutSec 3
        if ($resp.StatusCode -lt 400) { $up = $true; Write-Host " up."; break }
    } catch { }
    Write-Host -NoNewline "."; Start-Sleep -Seconds 2
}
if (-not $up) {
    Write-Host ""
    Write-Host "Didn't come up in time. Recent logs:"
    docker compose logs koetai --tail 50
    exit 1
}

# -- Done -------------------------------------------------------------------------
Say "6/6 Done"
Write-Host "Koetai is running at: http://localhost:$Port"
if ($Stores -contains "oxigraph") {
    Write-Host "When you create your first dataset, set its backend to 'oxigraph' in the New Dataset form."
}
Write-Host ""
Write-Host "Useful commands (run from $Dir):"
Write-Host "  docker compose logs -f koetai        # follow the app log"
Write-Host "  docker compose down                  # stop (keeps your data)"
$startArgs = (@("compose") + $ComposeProfile + @("up","-d","koetai") + $Stores) -join " "
Write-Host "  docker $startArgs   # start again"
Write-Host "  docker compose down -v               # stop AND delete all data"
Write-Host ""

if (YesNo "Open it in your browser now?" "y") {
    Start-Process "http://localhost:$Port"
}
