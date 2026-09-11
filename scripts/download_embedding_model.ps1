param(
    [Parameter(Mandatory = $false)]
    [string]$DataRoot
)

$ErrorActionPreference = 'Stop'

if (-not $DataRoot) {
    $DataRoot = if ($env:VOXAGENT_DATA_ROOT) { $env:VOXAGENT_DATA_ROOT } else { 'D:\VoxAgentData' }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$manifestPath = Join-Path $repoRoot 'backend\src\voxagent\memory\embedding_models.json'
$manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
$embeddingRoot = [IO.Path]::GetFullPath((Join-Path $DataRoot 'models\embeddings'))
$target = [IO.Path]::GetFullPath((Join-Path $embeddingRoot ([string]$manifest.Directory)))
$prefix = $embeddingRoot.TrimEnd('\') + '\'
if (-not $target.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Unsafe embedding model target: $target"
}

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Test-InstalledModel {
    if (-not (Test-Path -LiteralPath $target -PathType Container)) { return $false }
    $markerPath = Join-Path $target '.voxagent-complete'
    if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) { return $false }
    try {
        $marker = Get-Content -Raw -LiteralPath $markerPath | ConvertFrom-Json
        if ([string]$marker.revision -ne [string]$manifest.Revision) { return $false }
        foreach ($item in $manifest.Files) {
            $path = Join-Path $target ([string]$item.Name)
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $false }
            if ((Get-Item -LiteralPath $path).Length -ne [int64]$item.Bytes) { return $false }
            if ((Get-Sha256 -Path $path) -ne ([string]$item.Sha256).ToLowerInvariant()) {
                return $false
            }
        }
        return $true
    }
    catch {
        return $false
    }
}

New-Item -ItemType Directory -Force -Path $embeddingRoot | Out-Null
if (Test-InstalledModel) {
    Write-Host "Present: $($manifest.Name)"
    exit 0
}

$staging = Join-Path $embeddingRoot ".staging-$([Guid]::NewGuid().ToString('N'))"
$backup = "$target.backup-$([Guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Force -Path $staging | Out-Null
try {
    foreach ($item in $manifest.Files) {
        $destination = Join-Path $staging ([string]$item.Name)
        $partial = "$destination.part"
        Invoke-WebRequest -UseBasicParsing -Uri ([string]$item.Url) -OutFile $partial
        $actualBytes = (Get-Item -LiteralPath $partial).Length
        if ($actualBytes -ne [int64]$item.Bytes) {
            throw "Size mismatch for $($item.Name): expected $($item.Bytes), got $actualBytes"
        }
        $actualHash = Get-Sha256 -Path $partial
        if ($actualHash -ne ([string]$item.Sha256).ToLowerInvariant()) {
            throw "Checksum mismatch for $($item.Name)"
        }
        Move-Item -LiteralPath $partial -Destination $destination
    }
    [ordered]@{
        name = [string]$manifest.Name
        revision = [string]$manifest.Revision
        dimension = [int]$manifest.Dimension
        source = [string]$manifest.Source
    } | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath (
        Join-Path $staging '.voxagent-complete'
    )

    if (Test-Path -LiteralPath $target) {
        Move-Item -LiteralPath $target -Destination $backup
    }
    try {
        Move-Item -LiteralPath $staging -Destination $target
    }
    catch {
        if ((Test-Path -LiteralPath $backup) -and -not (Test-Path -LiteralPath $target)) {
            Move-Item -LiteralPath $backup -Destination $target
        }
        throw
    }
    if (Test-Path -LiteralPath $backup) {
        Remove-Item -LiteralPath $backup -Recurse -Force
    }
    Write-Host "Installed: $($manifest.Name)"
}
finally {
    if (Test-Path -LiteralPath $staging) {
        Remove-Item -LiteralPath $staging -Recurse -Force
    }
}
