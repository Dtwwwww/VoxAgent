param(
    [Parameter(Mandatory = $false)]
    [string]$DataRoot,

    [Parameter(Mandatory = $false)]
    [switch]$SkipPreflightForTests,

    [Parameter(Mandatory = $false)]
    [switch]$Quiet,

    [Parameter(Mandatory = $false)]
    [switch]$RequireVerifiedOllama,

    [Parameter(Mandatory = $false)]
    [switch]$AllowUnverifiedOllama,

    [Parameter(Mandatory = $false)]
    [object]$Command
)

$ErrorActionPreference = 'Stop'

if (-not $DataRoot) {
    if ($env:VOXAGENT_DATA_ROOT) {
        $DataRoot = $env:VOXAGENT_DATA_ROOT
    }
    else {
        $DataRoot = 'D:\VoxAgentData'
    }
}

$resolvedRoot = [IO.Path]::GetFullPath($DataRoot)
$paths = [ordered]@{
    data_root = $resolvedRoot
    model_root = Join-Path $resolvedRoot 'models'
    ollama_models = Join-Path $resolvedRoot 'models\ollama'
    speech_models = Join-Path $resolvedRoot 'models\speech'
    uv_cache = Join-Path $resolvedRoot 'cache\uv'
    temp = Join-Path $resolvedRoot 'cache\temp'
    pytest_temp = Join-Path $resolvedRoot 'cache\pytest'
}

foreach ($path in $paths.Values) {
    New-Item -ItemType Directory -Force -Path $path | Out-Null
}

$runtimeEnvironment = [ordered]@{
    VOXAGENT_DATA_ROOT = $paths.data_root
    VOXAGENT_MODEL_ROOT = $paths.model_root
    VOXAGENT_SPEECH_MODEL_ROOT = $paths.speech_models
    OLLAMA_MODELS = $paths.ollama_models
    OLLAMA_NO_CLOUD = '1'
    OLLAMA_HOST = '127.0.0.1:11434'
    UV_CACHE_DIR = $paths.uv_cache
    TEMP = $paths.temp
    TMP = $paths.temp
    VOXAGENT_PYTEST_TEMP = $paths.pytest_temp
}

foreach ($entry in $runtimeEnvironment.GetEnumerator()) {
    Set-Item -Path "Env:$($entry.Key)" -Value $entry.Value
}

if ($SkipPreflightForTests) {
    if ($env:VOXAGENT_ALLOW_TEST_PREFLIGHT_BYPASS -ne '1') {
        throw 'Test preflight bypass requires VOXAGENT_ALLOW_TEST_PREFLIGHT_BYPASS=1'
    }
    $preflightStatus = 'bypassed_for_tests'
}
else {
    $backendRoot = Join-Path (Split-Path -Parent $PSScriptRoot) 'backend'
    $preflightOutput = & uv run --project $backendRoot voxagent preflight --json --data-root $resolvedRoot
    if ($LASTEXITCODE -ne 0) {
        throw "VoxAgent preflight blocked runtime preparation: $preflightOutput"
    }
    $preflightStatus = 'passed'
}

if ($Command) {
    if ($Command -is [scriptblock]) {
        $commandBlock = $Command
    }
    elseif ($Command -is [string]) {
        $commandBlock = [scriptblock]::Create($Command)
    }
    else {
        throw '-Command must be a PowerShell script block or string.'
    }
    if ($RequireVerifiedOllama) {
        $backendRoot = Join-Path (Split-Path -Parent $PSScriptRoot) 'backend'
        $baselinePath = Join-Path (
            Split-Path -Parent $PSScriptRoot
        ) 'benchmarks\target-machine-baseline.json'
        $verificationOutput = & uv run --project $backendRoot voxagent verify-ollama-runtime `
            --data-root $resolvedRoot `
            --baseline $baselinePath `
            --json
        $verificationExitCode = $LASTEXITCODE
        if ($verificationExitCode -ne 0) {
            $onlyOfflineStateIsUnverified = $false
            try {
                $verification = ($verificationOutput -join [Environment]::NewLine) |
                    ConvertFrom-Json
                $issueCodes = @($verification.issues | ForEach-Object { [string]$_.code })
                $onlyOfflineStateIsUnverified = (
                    $issueCodes.Count -gt 0 -and
                    @($issueCodes | Where-Object {
                        $_ -ne 'ollama_offline_unverified'
                    }).Count -eq 0
                )
            }
            catch {
                $onlyOfflineStateIsUnverified = $false
            }
            $overrideAllowed = (
                $onlyOfflineStateIsUnverified -and
                $AllowUnverifiedOllama -and
                $env:VOXAGENT_ACCEPT_UNVERIFIED_OLLAMA -eq (
                    'I_ACCEPT_EXISTING_OLLAMA_WITH_UNVERIFIED_OFFLINE_STATE'
                )
            )
            if (-not $overrideAllowed) {
                throw "Ollama runtime verification blocked command: $verificationOutput"
            }
            Write-Warning (
                'Running with an explicitly accepted, unverified existing Ollama server. ' +
                'The wrapper did not change or restart that service.'
            )
        }
    }
    $LASTEXITCODE = 0
    & $commandBlock
    $commandSucceeded = $?
    $commandExitCode = $LASTEXITCODE
    if (-not $commandSucceeded) {
        exit $(if ($commandExitCode -ne 0) { $commandExitCode } else { 1 })
    }
    if ($commandExitCode -ne 0) {
        exit $commandExitCode
    }
    exit 0
}

if (-not $Quiet) {
    [ordered]@{
        data_root = $resolvedRoot
        preflight = $preflightStatus
        environment = $runtimeEnvironment
        behavior = (
            'Prepared directories and child-process environment only; no service was started, ' +
            'stopped, or reconfigured, and no existing Ollama service was claimed as verified.'
        )
    } | ConvertTo-Json -Depth 4
}
