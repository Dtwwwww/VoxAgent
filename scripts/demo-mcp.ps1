$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$BackendRoot = Join-Path $RepoRoot 'backend'
$VenvPython = Join-Path $BackendRoot '.venv\Scripts\python.exe'
$ExitCode = 2

Push-Location $BackendRoot
try {
    if (Test-Path -LiteralPath $VenvPython) {
        & $VenvPython -m voxagent.mcp.demo
    }
    else {
        uv run python -m voxagent.mcp.demo
    }
    $ExitCode = $LASTEXITCODE
}
catch {
    [Console]::Error.WriteLine('MCP demo environment error: ' + $_.Exception.Message)
    $ExitCode = 2
}
finally {
    Pop-Location
}

exit $ExitCode
