$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue

if (-not $wsl) {
    throw (
        "WSL is not installed or wsl.exe is not on PATH. " +
        "The release workflow requires WSL."
    )
}

Write-Host "[release] entering WSL for release checks, build, and upload"
& $wsl.Source --cd $repoRoot /usr/bin/python3 scripts/release.py @args
$releaseExitCode = $LASTEXITCODE

if ($releaseExitCode -ne 0) {
    exit $releaseExitCode
}
