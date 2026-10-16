<#
.SYNOPSIS
  Build the Windows one-folder distribution of kioskium with PyInstaller.

.DESCRIPTION
  Creates (or reuses) .venv in the repository root, installs the pinned versions from
  requirements\windows.txt into it and builds. Never touches the global Python installation.
  Output: <repo>\dist\windows-x86_64\kiosk\kiosk.exe and kioskctl.exe (ship the whole kiosk folder).

.PARAMETER Console
  Build a console variant (logs printed to the terminal) instead of the windowed kiosk build.
.PARAMETER Clean
  Remove the previous Windows build (build\windows-x86_64, dist\windows-x86_64) first.
.PARAMETER Python
  Python used to create .venv when it does not exist yet (default: py -3.14, else python).
#>
param(
    [switch]$Console,
    [switch]$Clean,
    [string]$Python
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Push-Location $root
try {
    $venvPython = Join-Path $root ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        if (-not $Python) {
            $Python = if (Get-Command py -ErrorAction SilentlyContinue) { "py -3.14" } else { "python" }
        }
        Write-Host "Creating .venv with $Python"
        Invoke-Expression "$Python -m venv .venv"
        if ($LASTEXITCODE -ne 0) { throw "could not create .venv" }
    }

    & $venvPython -m pip install --upgrade pip --quiet
    & $venvPython -m pip install --quiet -r requirements\windows.txt
    if ($LASTEXITCODE -ne 0) { throw "pip install -r requirements\windows.txt failed" }
    & $venvPython -m pip install --quiet --no-deps -e .
    if ($LASTEXITCODE -ne 0) { throw "pip install -e . failed" }

    if ($Clean) {
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build\windows-x86_64, dist\windows-x86_64
    }
    if ($Console) { $env:KIOSKIUM_CONSOLE = "1" } else { Remove-Item Env:KIOSKIUM_CONSOLE -ErrorAction SilentlyContinue }

    & $venvPython -m PyInstaller packaging\pyinstaller\kioskium.spec --noconfirm --clean `
        --distpath dist\windows-x86_64 --workpath build\windows-x86_64
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

    Write-Host ""
    Write-Host "Built: $(Join-Path $root 'dist\windows-x86_64\kiosk\kiosk.exe')"
    Write-Host "Try:   dist\windows-x86_64\kiosk\kiosk.exe --windowed kioskium:test"
}
finally {
    Pop-Location
}
