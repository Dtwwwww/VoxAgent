from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SpeechModel:
    name: str
    archive_name: str
    url: str
    directory_name: str
    version: str
    source: str
    archive_sha256: str
    required_files: tuple[str, ...]


SPEECH_MODELS = (
    SpeechModel(
        name="sensevoice-int8",
        archive_name="sensevoice-int8.tar.bz2",
        url="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
        "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2",
        directory_name="sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17",
        version="2024-07-17",
        source="k2-fsa/sherpa-onnx release asr-models",
        archive_sha256="7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e",
        required_files=("model.int8.onnx", "tokens.txt"),
    ),
    SpeechModel(
        name="kokoro-int8-zh-en",
        archive_name="kokoro-int8-multi-lang-v1_1.tar.bz2",
        url="https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"
        "kokoro-int8-multi-lang-v1_1.tar.bz2",
        directory_name="kokoro-int8-multi-lang-v1_1",
        version="1.1",
        source="k2-fsa/sherpa-onnx release tts-models",
        archive_sha256="a1e94694776049035c4f2c6529f003aaece993c76aae9a78995831c3c4dcafc6",
        required_files=("model.int8.onnx", "voices.bin", "tokens.txt", "lexicon-zh.txt"),
    ),
    SpeechModel(
        name="melo-zh-en",
        archive_name="vits-melo-tts-zh_en.tar.bz2",
        url="https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"
        "vits-melo-tts-zh_en.tar.bz2",
        directory_name="vits-melo-tts-zh_en",
        version="vits-melo-tts-zh_en",
        source="k2-fsa/sherpa-onnx release tts-models",
        archive_sha256="e58351ed7149f290a54534538badd4077cdbe6fddc964b24d0bee870415d1514",
        required_files=("model.onnx", "tokens.txt", "lexicon.txt"),
    ),
)
