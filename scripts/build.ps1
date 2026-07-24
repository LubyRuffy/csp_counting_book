$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$forwardArgs = @($args)
$engine = "auto"

for ($index = 0; $index -lt $forwardArgs.Count; $index++) {
    if (
        $forwardArgs[$index] -in @("--engine", "-engine") -and
        $index + 1 -lt $forwardArgs.Count
    ) {
        $engine = $forwardArgs[$index + 1]
        break
    }
}

if (
    -not ($forwardArgs -contains "--engine") -and
    -not ($forwardArgs -contains "-engine")
) {
    $forwardArgs = @("--engine", "auto") + $forwardArgs
}

if ($engine -eq "docker") {
    Write-Host "[book] using Docker from Windows"
    Push-Location $repoRoot
    try {
        python scripts/build_book.py build @forwardArgs
        $buildExitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
}
else {
    $wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
    if (-not $wsl) {
        throw (
            "WSL is not installed or wsl.exe is not on PATH. " +
            "Install WSL, or explicitly use Docker with " +
            "'.\scripts\build.ps1 --engine docker'."
        )
    }

    Write-Host "[book] entering WSL for the local ebook build"
    & $wsl.Source --cd $repoRoot /usr/bin/python3 `
        scripts/build_book.py build @forwardArgs
    $buildExitCode = $LASTEXITCODE
}

if ($buildExitCode -ne 0) {
    exit $buildExitCode
}
