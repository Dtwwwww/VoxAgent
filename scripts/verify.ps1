param(
    [ValidateSet('All', 'Backend', 'Frontend')]
    [string]$Scope = 'All'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Failures = [System.Collections.Generic.List[string]]::new()

function Invoke-Gate {
    param(
        [string]$Name,
        [string]$WorkingDirectory,
        [scriptblock]$Command
    )

    Write-Host "==> $Name"
    Push-Location $WorkingDirectory
    try {
        & $Command
        if ($LASTEXITCODE -ne 0) {
            $Failures.Add($Name)
            Write-Warning "$Name exited with code $LASTEXITCODE"
        }
    }
    catch {
        $Failures.Add($Name)
        Write-Warning "$Name failed to start: $($_.Exception.GetType().Name)"
    }
    finally {
        Pop-Location
    }
}

$BackendRoot = Join-Path $RepoRoot 'backend'
$FrontendRoot = Join-Path $RepoRoot 'frontend'
$VenvPython = Join-Path $BackendRoot '.venv\Scripts\python.exe'
$VenvRuff = Join-Path $BackendRoot '.venv\Scripts\ruff.exe'
$VitestCmd = Join-Path $FrontendRoot 'node_modules\.bin\vitest.cmd'
$TscCmd = Join-Path $FrontendRoot 'node_modules\.bin\tsc.cmd'
$ViteCmd = Join-Path $FrontendRoot 'node_modules\.bin\vite.cmd'

if ($Scope -in @('All', 'Backend')) {
    $PytestTemp = Join-Path ([System.IO.Path]::GetTempPath()) ("voxagent-pytest-$([guid]::NewGuid().ToString('N'))")
    Invoke-Gate 'backend-pytest' $BackendRoot {
        if (Test-Path -LiteralPath $VenvPython) {
            & $VenvPython -m pytest -q --basetemp $PytestTemp -p no:cacheprovider
        }
        else {
            uv run --extra dev pytest -q --basetemp $PytestTemp -p no:cacheprovider
        }
    }
    Invoke-Gate 'backend-ruff' $BackendRoot {
        if (Test-Path -LiteralPath $VenvRuff) {
            & $VenvRuff check src tests
        }
        else {
            uv run --extra dev ruff check src tests
        }
    }
}

if ($Scope -in @('All', 'Frontend')) {
    Invoke-Gate 'frontend-vitest' $FrontendRoot {
        if (Test-Path -LiteralPath $VitestCmd) {
            & $VitestCmd run
        }
        else {
            pnpm exec vitest run
        }
    }
    Invoke-Gate 'frontend-typecheck' $FrontendRoot {
        if (Test-Path -LiteralPath $TscCmd) {
            & $TscCmd --noEmit
        }
        else {
            pnpm exec tsc --noEmit
        }
    }
    Invoke-Gate 'frontend-build' $FrontendRoot {
        if (Test-Path -LiteralPath $ViteCmd) {
            & $ViteCmd build
        }
        else {
            pnpm exec vite build
        }
    }
}

if ($Failures.Count -gt 0) {
    [Console]::Error.WriteLine('Verification failed: ' + ($Failures -join ', '))
    exit 1
}

Write-Host 'Verification passed.'
