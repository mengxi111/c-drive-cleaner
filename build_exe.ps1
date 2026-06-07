param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppPath = Join-Path $ProjectRoot "c_drive_cleaner.py"

if (-not (Test-Path -LiteralPath $AppPath)) {
    throw "Cannot find c_drive_cleaner.py"
}

Set-Location -LiteralPath $ProjectRoot

Write-Host "Checking PyInstaller..."
$hasPyInstaller = python -c "import importlib.util; print('yes' if importlib.util.find_spec('PyInstaller') else 'no')"
if ($hasPyInstaller.Trim() -ne "yes") {
    Write-Host "Installing PyInstaller..."
    python -m pip install --upgrade pyinstaller
}

if ($Clean) {
    foreach ($path in @("build", "dist")) {
        $target = Join-Path $ProjectRoot $path
        if (Test-Path -LiteralPath $target) {
            Write-Host "Removing $target"
            Remove-Item -LiteralPath $target -Recurse -Force
        }
    }
    $spec = Join-Path $ProjectRoot "CDriveCleaner.spec"
    if (Test-Path -LiteralPath $spec) {
        Remove-Item -LiteralPath $spec -Force
    }
}

Write-Host "Building EXE..."
python -m PyInstaller `
    --noconfirm `
    --windowed `
    --name "CDriveCleaner" `
    --clean `
    "$AppPath"

$ExePath = Join-Path $ProjectRoot "dist\CDriveCleaner\CDriveCleaner.exe"
if (-not (Test-Path -LiteralPath $ExePath)) {
    throw "Build finished but EXE was not found: $ExePath"
}

Write-Host ""
Write-Host "Build complete:"
Write-Host $ExePath
