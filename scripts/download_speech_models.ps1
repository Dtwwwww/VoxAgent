param(
    [Parameter(Mandatory = $false)]
    [string]$DataRoot,

    [Parameter(Mandatory = $false)]
    [string]$ModelManifestPath,

    [Parameter(Mandatory = $false)]
    [string[]]$ModelName,

    [Parameter(Mandatory = $false)]
    [switch]$SkipPreflightForTests,

    [Parameter(Mandatory = $false)]
    [switch]$AllowCustomManifestForTests
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

$defaultManifestPath = Join-Path (
    Split-Path -Parent $PSScriptRoot
) 'backend\src\voxagent\speech\models.json'
if ($ModelManifestPath) {
    $customManifestAllowed = (
        $AllowCustomManifestForTests -and
        $SkipPreflightForTests -and
        $env:VOXAGENT_ALLOW_TEST_MODEL_MANIFEST -eq '1' -and
        $env:VOXAGENT_ALLOW_TEST_PREFLIGHT_BYPASS -eq '1'
    )
    if (-not $customManifestAllowed) {
        throw 'Custom model manifests are disabled; the explicit test-only double gate is required.'
    }
}
else {
    $ModelManifestPath = $defaultManifestPath
}

$runtimeBootstrap = Join-Path $PSScriptRoot 'voxagent_runtime.ps1'

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

function Assert-ContainedPath {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    $pathFull = [IO.Path]::GetFullPath($Path)
    $prefix = $rootFull + [IO.Path]::DirectorySeparatorChar
    if (-not $pathFull.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe $Label path outside selected root: $Path"
    }
    return $pathFull
}

function Resolve-ManifestPath {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$RelativePath,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if ([string]::IsNullOrWhiteSpace($RelativePath) -or
        [IO.Path]::IsPathRooted($RelativePath) -or
        $RelativePath -match '^[A-Za-z]:' -or
        @($RelativePath -split '[\\/]').Contains('..')) {
        throw "Unsafe manifest path for $Label`: $RelativePath"
    }
    $candidate = Join-Path $Root $RelativePath
    return Assert-ContainedPath -Root $Root -Path $candidate -Label "manifest $Label"
}

function Assert-SafeLeafName {
    param(
        [Parameter(Mandatory = $true)][string]$Value,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if ([string]::IsNullOrWhiteSpace($Value) -or
        [IO.Path]::IsPathRooted($Value) -or
        $Value -match '[\\/]' -or
        $Value -in @('.', '..')) {
        throw "Unsafe manifest path for $Label`: $Value"
    }
}

function Test-RequiredFiles {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)]$RequiredFiles
    )
    foreach ($relativePath in @($RequiredFiles)) {
        $candidate = Resolve-ManifestPath `
            -Root $Root `
            -RelativePath ([string]$relativePath) `
            -Label 'RequiredFiles'
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
        $markerMatches = (
            ([string]$marker.version -eq [string]$Model.Version) -and
            ([string]$marker.archive_sha256 -eq ([string]$Model.ArchiveSha256).ToLowerInvariant())
        )
        if (-not $markerMatches) {
            return $false
        }
        $modelFormat = if ([string]::IsNullOrWhiteSpace([string]$Model.Format)) {
            'archive'
        }
        else {
            [string]$Model.Format
        }
        if ($modelFormat -eq 'file') {
            $assetPath = Resolve-ManifestPath `
                -Root $Target `
                -RelativePath ([string]$Model.Archive) `
                -Label 'completed file asset'
            return (
                (Test-Path -LiteralPath $assetPath -PathType Leaf) -and
                ((Get-ArchiveSha256 -Path $assetPath) -eq ([string]$Model.ArchiveSha256).ToLowerInvariant())
            )
        }
        return $true
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
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Reason
    )
    if (Test-Path -LiteralPath $Path) {
        $safePath = Assert-ContainedPath -Root $Root -Path $Path -Label 'quarantine source'
        $quarantinePath = "$Path.corrupt-$Reason-$([Guid]::NewGuid().ToString('N'))"
        $safeQuarantine = Assert-ContainedPath `
            -Root $Root `
            -Path $quarantinePath `
            -Label 'quarantine destination'
        Move-Item -LiteralPath $safePath -Destination $safeQuarantine
    }
}

function Assert-SafeArchive {
    param(
        [Parameter(Mandatory = $true)][string]$ArchivePath,
        [Parameter(Mandatory = $true)][string]$ExtractionRoot
    )
    $members = @(& tar.exe -tjf $ArchivePath)
    if ($LASTEXITCODE -ne 0) {
        throw "tar.exe could not enumerate archive members (exit $LASTEXITCODE)"
    }
    $verboseMembers = @(& tar.exe -tvjf $ArchivePath)
    if ($LASTEXITCODE -ne 0) {
        throw "tar.exe could not inspect archive member types (exit $LASTEXITCODE)"
    }
    foreach ($line in $verboseMembers) {
        $entry = ([string]$line).TrimStart()
        if ($entry -and $entry[0] -notin @('-', 'd')) {
            throw "Unsafe archive member type (link/reparse/special): $line"
        }
    }
    foreach ($memberValue in $members) {
        $member = ([string]$memberValue).Trim()
        $normalized = $member.Replace('\', '/')
        if (-not $member -or
            $normalized.StartsWith('/') -or
            $normalized -match '^[A-Za-z]:' -or
            @($normalized -split '/').Contains('..')) {
            throw "Unsafe archive member path: $member"
        }
        $null = Resolve-ManifestPath `
            -Root $ExtractionRoot `
            -RelativePath $normalized `
            -Label 'archive member'
    }
}

$manifestModels = Get-Content -Raw -LiteralPath $ModelManifestPath | ConvertFrom-Json
$allModels = [System.Collections.Generic.List[object]]::new()
foreach ($manifestModel in $manifestModels) {
    $allModels.Add($manifestModel)
}
if ($PSBoundParameters.ContainsKey('ModelName')) {
    if (@($ModelName).Count -eq 0) {
        throw 'ModelName must not be empty.'
    }
    $models = [System.Collections.Generic.List[object]]::new()
    foreach ($requestedModelName in $ModelName) {
        if ([string]::IsNullOrWhiteSpace($requestedModelName)) {
            throw 'ModelName must not be blank.'
        }
        $matchedModel = $null
        foreach ($candidateModel in $allModels) {
            if (([string]$candidateModel.Name) -ceq ([string]$requestedModelName)) {
                if ($matchedModel) {
                    throw "Model manifest contains duplicate name: $requestedModelName"
                }
                $matchedModel = $candidateModel
            }
        }
        if ($null -eq $matchedModel) {
            throw "Unknown model name: $requestedModelName"
        }
        $models.Add($matchedModel)
    }
}
else {
    $models = $allModels
}

& $runtimeBootstrap -DataRoot $DataRoot -Quiet -SkipPreflightForTests:$SkipPreflightForTests

$modelRoot = [IO.Path]::GetFullPath((Join-Path $DataRoot 'models\speech'))
$stagingRoot = Assert-ContainedPath `
    -Root $modelRoot `
    -Path (Join-Path $modelRoot '.staging') `
    -Label 'staging root'
New-Item -ItemType Directory -Force -Path $modelRoot | Out-Null
New-Item -ItemType Directory -Force -Path $stagingRoot | Out-Null

foreach ($model in $models) {
    Assert-SafeLeafName -Value ([string]$model.Name) -Label 'Name'
    Assert-SafeLeafName -Value ([string]$model.Archive) -Label 'Archive'
    Assert-SafeLeafName -Value ([string]$model.Directory) -Label 'Directory'
    $modelFormat = if ([string]::IsNullOrWhiteSpace([string]$model.Format)) {
        'archive'
    }
    else {
        [string]$model.Format
    }
    if ($modelFormat -notin @('archive', 'file')) {
        throw "Invalid Format for $($model.Name): $modelFormat"
    }
    if (-not ([string]$model.ArchiveSha256 -match '^[0-9a-fA-F]{64}$')) {
        throw "Invalid ArchiveSha256 for $($model.Name)"
    }
    if (@($model.RequiredFiles).Count -eq 0) {
        throw "RequiredFiles must not be empty for $($model.Name)"
    }
    foreach ($requiredFile in @($model.RequiredFiles)) {
        $null = Resolve-ManifestPath `
            -Root $modelRoot `
            -RelativePath ([string]$requiredFile) `
            -Label 'RequiredFiles'
    }

    $target = Resolve-ManifestPath `
        -Root $modelRoot `
        -RelativePath ([string]$model.Directory) `
        -Label 'Directory'
    if (Test-CompletedModel -Target $target -Model $model) {
        Write-Host "Present: $($model.Name)"
        continue
    }

    $archive = Resolve-ManifestPath `
        -Root $modelRoot `
        -RelativePath ([string]$model.Archive) `
        -Label 'Archive'
    $partialArchive = Assert-ContainedPath `
        -Root $modelRoot `
        -Path "$archive.part" `
        -Label 'partial archive'
    $expectedHash = ([string]$model.ArchiveSha256).ToLowerInvariant()

    if (Test-Path -LiteralPath $partialArchive) {
        $null = Assert-ContainedPath `
            -Root $modelRoot `
            -Path $partialArchive `
            -Label 'partial archive removal'
        Remove-Item -LiteralPath $partialArchive -Force
    }
    if ((Test-Path -LiteralPath $archive -PathType Leaf) -and
        ((Get-ArchiveSha256 -Path $archive) -ne $expectedHash)) {
        Move-ToQuarantine -Root $modelRoot -Path $archive -Reason 'checksum'
    }

    if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) {
        Copy-ArchiveSource -Source ([string]$model.Url) -Destination $partialArchive
        $downloadedHash = Get-ArchiveSha256 -Path $partialArchive
        if ($downloadedHash -ne $expectedHash) {
            Move-ToQuarantine -Root $modelRoot -Path $partialArchive -Reason 'download'
            throw "Checksum mismatch for $($model.Name): expected $expectedHash, got $downloadedHash"
        }
        $null = Assert-ContainedPath -Root $modelRoot -Path $partialArchive -Label 'download publish'
        $null = Assert-ContainedPath -Root $modelRoot -Path $archive -Label 'archive publish'
        Move-Item -LiteralPath $partialArchive -Destination $archive
    }

    $staging = Resolve-ManifestPath `
        -Root $stagingRoot `
        -RelativePath "$($model.Name)-$([Guid]::NewGuid().ToString('N'))" `
        -Label 'staging directory'
    New-Item -ItemType Directory -Force -Path $staging | Out-Null
    $published = $false
    try {
        $extractedTarget = Resolve-ManifestPath `
            -Root $staging `
            -RelativePath ([string]$model.Directory) `
            -Label 'staged model'
        if ($modelFormat -eq 'archive') {
            Assert-SafeArchive -ArchivePath $archive -ExtractionRoot $staging
            & tar.exe -xjf $archive -C $staging
            if ($LASTEXITCODE -ne 0) {
                throw "tar.exe exited with code $LASTEXITCODE"
            }
            if (-not (Test-Path -LiteralPath $extractedTarget -PathType Container)) {
                throw "archive did not contain $($model.Directory)"
            }
        }
        else {
            New-Item -ItemType Directory -Force -Path $extractedTarget | Out-Null
            $stagedAsset = Resolve-ManifestPath `
                -Root $extractedTarget `
                -RelativePath ([string]$model.Archive) `
                -Label 'staged file asset'
            Copy-Item -LiteralPath $archive -Destination $stagedAsset
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
            $backup = Assert-ContainedPath `
                -Root $modelRoot `
                -Path "$target.backup-$([Guid]::NewGuid().ToString('N'))" `
                -Label 'model backup'
            $null = Assert-ContainedPath -Root $modelRoot -Path $target -Label 'backup source'
            Move-Item -LiteralPath $target -Destination $backup
        }
        try {
            $null = Assert-ContainedPath `
                -Root $staging `
                -Path $extractedTarget `
                -Label 'model publish source'
            $null = Assert-ContainedPath -Root $modelRoot -Path $target -Label 'model publish target'
            Move-Item -LiteralPath $extractedTarget -Destination $target
            $published = $true
        }
        catch {
            if ($backup -and (-not (Test-Path -LiteralPath $target))) {
                $null = Assert-ContainedPath -Root $modelRoot -Path $backup -Label 'backup restore'
                $null = Assert-ContainedPath -Root $modelRoot -Path $target -Label 'restore target'
                Move-Item -LiteralPath $backup -Destination $target
            }
            throw
        }
        if ($backup) {
            $null = Assert-ContainedPath -Root $modelRoot -Path $backup -Label 'backup removal'
            Remove-Item -LiteralPath $backup -Recurse -Force
        }
    }
    catch {
        if (-not $published) {
            Move-ToQuarantine -Root $modelRoot -Path $archive -Reason 'extraction'
        }
        throw "Extraction failed for $($model.Name): $($_.Exception.Message)"
    }
    finally {
        if (Test-Path -LiteralPath $staging) {
            $null = Assert-ContainedPath -Root $stagingRoot -Path $staging -Label 'staging removal'
            Remove-Item -LiteralPath $staging -Recurse -Force
        }
    }
}
