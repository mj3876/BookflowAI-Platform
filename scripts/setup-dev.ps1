param(
    [switch]$Recreate,
    [switch]$RemoveOldVenvs,
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$VenvPath = Join-Path $RepoRoot ".venv"
$RequirementsPath = Join-Path $ScriptDir "requirements.txt"
$OldVenvPaths = @(
    (Join-Path $RepoRoot "venv"),
    (Join-Path $ScriptDir ".venv")
)

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message"
}

function Assert-Command {
    param([string]$Command)
    $resolved = Get-Command $Command -ErrorAction SilentlyContinue
    if (-not $resolved) {
        throw "Command '$Command' was not found. Install Python 3.11+ or pass -Python with the full python.exe path."
    }
}

if (-not (Test-Path -LiteralPath $RequirementsPath)) {
    throw "Requirements file not found: $RequirementsPath"
}

Write-Step "Checking Python"
Assert-Command $Python
& $Python --version

if ($Recreate -and (Test-Path -LiteralPath $VenvPath)) {
    Write-Step "Removing existing root virtual environment"
    Remove-Item -LiteralPath $VenvPath -Recurse -Force
}

if (-not (Test-Path -LiteralPath $VenvPath)) {
    Write-Step "Creating root virtual environment"
    & $Python -m venv $VenvPath
}
else {
    Write-Step "Using existing root virtual environment"
}

$VenvPython = Join-Path $VenvPath "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw "Virtual environment python.exe not found: $VenvPython"
}

Write-Step "Upgrading pip"
& $VenvPython -m pip install --upgrade pip

Write-Step "Installing project Python dependencies"
& $VenvPython -m pip install -r $RequirementsPath

if ($RemoveOldVenvs) {
    foreach ($oldPath in $OldVenvPaths) {
        if ((Test-Path -LiteralPath $oldPath) -and ($oldPath -ne $VenvPath)) {
            Write-Step "Removing old virtual environment: $oldPath"
            Remove-Item -LiteralPath $oldPath -Recurse -Force
        }
    }
}
else {
    $existingOldVenvs = $OldVenvPaths | Where-Object { Test-Path -LiteralPath $_ }
    if ($existingOldVenvs.Count -gt 0) {
        Write-Host ""
        Write-Host "Old virtual environments were left untouched:"
        $existingOldVenvs | ForEach-Object { Write-Host "  $_" }
        Write-Host "Run with -RemoveOldVenvs after verifying .venv works."
    }
}

Write-Host ""
Write-Host "Setup complete."
Write-Host "Activate with:"
Write-Host "  .\.venv\Scripts\Activate.ps1"
