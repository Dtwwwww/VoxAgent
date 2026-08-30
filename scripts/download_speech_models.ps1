param(
    [Parameter(Mandatory = $false)]
    [string]$DataRoot = 'D:\VoxAgentData',

    [Parameter(Mandatory = $false)]
    [string]$ModelManifestPath
)

$ErrorActionPreference = 'Stop'

function Get-ArchiveSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    $stream = [IO.File]::OpenRead($Path)
    try {
        $sha256 = [Security.Cryptography.SHA256]::Create()
        try {
            $digest = $sha256.ComputeHash($stream)
            return ([BitConverter]::ToString($digest) -replace '-', '').ToLowerInvariant()
        }
        finally {
            $sha256.Dispose()
        }
    }
    finally {
        $stream.Dispose()
    }
}

function Test-RequiredFiles {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)]$RequiredFiles
    )
    foreach ($relativePath in @($RequiredFiles)) {
        $candidate = Join-Path $Root ([string]$relativePath)
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return $false
        }
    }
    return $true
}

function Test-CompletedModel {
    param(
        [Parameter(Mandatory = $true)][string]$Target,
        [Parameter(Mandatory = $true)]$Model
    )
    $markerPath = Join-Path $Target '.voxagent-complete'
    if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) {
        return $false
    }
    if (-not (Test-RequiredFiles -Root $Target -RequiredFiles $Model.RequiredFiles)) {
        return $false
    }
    try {
        $marker = Get-Content -Raw -LiteralPath $markerPath | ConvertFrom-Json
        return (
            ([string]$marker.version -eq [string]$Model.Version) -and
            ([string]$marker.archive_sha256 -eq ([string]$Model.ArchiveSha256).ToLowerInvariant())
        )
    }
    catch {
        return $false
    }
}

function Copy-ArchiveSource {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    if (Test-Path -LiteralPath $Source -PathType Leaf) {
        Copy-Item -LiteralPath $Source -Destination $Destination
        return
    }
    $sourceUri = $null
    if ([Uri]::TryCreate($Source, [UriKind]::Absolute, [ref]$sourceUri) -and
        $sourceUri.Scheme -eq 'file') {
        Copy-Item -LiteralPath $sourceUri.LocalPath -Destination $Destination
        return
    }
    Invoke-WebRequest -UseBasicParsing -Uri $Source -OutFile $Destination
}

function Move-ToQuarantine {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Reason
    )
    if (Test-Path -LiteralPath $Path) {
        $quarantinePath = "$Path.corrupt-$Reason-$([Guid]::NewGuid().ToString('N'))"
        Move-Item -LiteralPath $Path -Destination $quarantinePath
    }
}

$models = @(
    @{
        Name = 'sensevoice-int8'
        Archive = 'sensevoice-int8.tar.bz2'
        Directory = 'sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17'
        Url = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2'
        Version = '2024-07-17'
        ArchiveSha256 = '7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e'
        RequiredFiles = @('model.int8.onnx', 'tokens.txt')
    },
    @{
        Name = 'kokoro-int8-zh-en'
        Archive = 'kokoro-int8-multi-lang-v1_1.tar.bz2'
        Directory = 'kokoro-int8-multi-lang-v1_1'
        Url = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/kokoro-int8-multi-lang-v1_1.tar.bz2'
        Version = '1.1'
        ArchiveSha256 = 'a1e94694776049035c4f2c6529f003aaece993c76aae9a78995831c3c4dcafc6'
        RequiredFiles = @('model.int8.onnx', 'voices.bin', 'tokens.txt', 'lexicon-zh.txt')
    },
    @{
        Name = 'melo-zh-en'
        Archive = 'vits-melo-tts-zh_en.tar.bz2'
        Directory = 'vits-melo-tts-zh_en'
        Url = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-melo-tts-zh_en.tar.bz2'
        Version = 'vits-melo-tts-zh_en'
        ArchiveSha256 = 'e58351ed7149f290a54534538badd4077cdbe6fddc964b24d0bee870415d1514'
        RequiredFiles = @('model.onnx', 'tokens.txt', 'lexicon.txt')
    }
)

if ($ModelManifestPath) {
    $models = @(Get-Content -Raw -LiteralPath $ModelManifestPath | ConvertFrom-Json)
}

$modelRoot = Join-Path $DataRoot 'models\speech'
$stagingRoot = Join-Path $modelRoot '.staging'
New-Item -ItemType Directory -Force -Path $modelRoot | Out-Null
New-Item -ItemType Directory -Force -Path $stagingRoot | Out-Null

foreach ($model in $models) {
    $target = Join-Path $modelRoot ([string]$model.Directory)
    if (Test-CompletedModel -Target $target -Model $model) {
        Write-Host "Present: $($model.Name)"
        continue
    }

    $archive = Join-Path $modelRoot ([string]$model.Archive)
    $partialArchive = "$archive.part"
    $expectedHash = ([string]$model.ArchiveSha256).ToLowerInvariant()

    if (Test-Path -LiteralPath $partialArchive) {
        Remove-Item -LiteralPath $partialArchive -Force
    }
    if ((Test-Path -LiteralPath $archive -PathType Leaf) -and
        ((Get-ArchiveSha256 -Path $archive) -ne $expectedHash)) {
        Move-ToQuarantine -Path $archive -Reason 'checksum'
    }

    if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) {
        Copy-ArchiveSource -Source ([string]$model.Url) -Destination $partialArchive
        $downloadedHash = Get-ArchiveSha256 -Path $partialArchive
        if ($downloadedHash -ne $expectedHash) {
            Move-ToQuarantine -Path $partialArchive -Reason 'download'
            throw "Checksum mismatch for $($model.Name): expected $expectedHash, got $downloadedHash"
        }
        Move-Item -LiteralPath $partialArchive -Destination $archive
    }

    $staging = Join-Path $stagingRoot "$($model.Name)-$([Guid]::NewGuid().ToString('N'))"
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    $published = $false
    try {
        & tar.exe -xjf $archive -C $staging
        if ($LASTEXITCODE -ne 0) {
            throw "tar.exe exited with code $LASTEXITCODE"
        }
        $extractedTarget = Join-Path $staging ([string]$model.Directory)
        if (-not (Test-Path -LiteralPath $extractedTarget -PathType Container)) {
            throw "archive did not contain $($model.Directory)"
        }
        if (-not (Test-RequiredFiles -Root $extractedTarget -RequiredFiles $model.RequiredFiles)) {
            throw 'one or more required files are missing'
        }

        $marker = [ordered]@{
            name = [string]$model.Name
            version = [string]$model.Version
            archive_sha256 = $expectedHash
            source = [string]$model.Url
        }
        $marker | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath (
            Join-Path $extractedTarget '.voxagent-complete'
        )

        $backup = $null
        if (Test-Path -LiteralPath $target) {
            $backup = "$target.backup-$([Guid]::NewGuid().ToString('N'))"
            Move-Item -LiteralPath $target -Destination $backup
        }
        try {
            Move-Item -LiteralPath $extractedTarget -Destination $target
            $published = $true
        }
        catch {
            if ($backup -and (-not (Test-Path -LiteralPath $target))) {
                Move-Item -LiteralPath $backup -Destination $target
            }
            throw
        }
        if ($backup) {
            Remove-Item -LiteralPath $backup -Recurse -Force
        }
    }
    catch {
        if (-not $published) {
            Move-ToQuarantine -Path $archive -Reason 'extraction'
        }
        throw "Extraction failed for $($model.Name): $($_.Exception.Message)"
    }
    finally {
        if (Test-Path -LiteralPath $staging) {
            Remove-Item -LiteralPath $staging -Recurse -Force
        }
    }
}
