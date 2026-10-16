<#
.SYNOPSIS
  Build the self-contained aarch64 Linux binary of kioskium in an arm64 Docker container.

.DESCRIPTION
  Needs Docker Desktop (arm64 emulation is built in on Windows and macOS). Output:
    dist\linux-arm64\kiosk\                     one-folder bundle (own Python, Qt, Chromium)
    dist\linux-arm64\kiosk-linux-arm64.tar.gz   the same, as an archive to copy to a device
  The first run builds the image (about 10 min); each emulated build then takes 10-20 min.
  On an arm64 host (or the ubuntu-24.04-arm GitHub runner) it runs natively and much faster.
#>
param([switch]$NoCache)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Push-Location $root
try {
    $buildArgs = @("build", "--platform", "linux/arm64", "-t", "kioskium-build-arm64", "-f", "packaging/linux-arm64/Dockerfile", "packaging/linux-arm64")
    if ($NoCache) { $buildArgs += "--no-cache" }
    docker @buildArgs
    if ($LASTEXITCODE -ne 0) { throw "docker build failed" }

    docker run --rm --platform linux/arm64 -v "${root}:/src" kioskium-build-arm64
    if ($LASTEXITCODE -ne 0) { throw "build inside the container failed" }

    Write-Host ""
    Write-Host "Built: $(Join-Path $root 'dist\linux-arm64\kiosk')"
    Write-Host "Copy dist\linux-arm64\kiosk-linux-arm64.tar.gz to the device and run:"
    Write-Host "  tar -xzf kiosk-linux-arm64.tar.gz && ./kiosk/kiosk --app-id my-kiosk URL"
}
finally {
    Pop-Location
}
