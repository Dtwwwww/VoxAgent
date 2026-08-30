param(
    [Parameter(Mandatory = $false)]
    [string]$DataRoot = 'D:\VoxAgentData'
)

$ErrorActionPreference = 'Stop'
$modelRoot = Join-Path $DataRoot 'models\speech'
New-Item -ItemType Directory -Force -Path $modelRoot | Out-Null

$models = @(
    @{
        Name = 'sensevoice-int8'
        Archive = 'sensevoice-int8.tar.bz2'
        Directory = 'sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17'
        Url = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2'
    },
    @{
        Name = 'kokoro-int8-zh-en'
        Archive = 'kokoro-int8-multi-lang-v1_1.tar.bz2'
        Directory = 'kokoro-int8-multi-lang-v1_1'
        Url = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/kokoro-int8-multi-lang-v1_1.tar.bz2'
    },
    @{
        Name = 'melo-zh-en'
        Archive = 'vits-melo-tts-zh_en.tar.bz2'
        Directory = 'vits-melo-tts-zh_en'
        Url = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-melo-tts-zh_en.tar.bz2'
    }
)

foreach ($model in $models) {
    $target = Join-Path $modelRoot $model.Directory
    if (Test-Path -LiteralPath $target) {
        Write-Host "Present: $($model.Name)"
        continue
    }
    $archive = Join-Path $modelRoot $model.Archive
    Invoke-WebRequest -Uri $model.Url -OutFile $archive
    tar.exe -xjf $archive -C $modelRoot
    Remove-Item -LiteralPath $archive
    if (-not (Test-Path -LiteralPath $target)) {
        throw "Extraction failed for $($model.Name)"
    }
}
