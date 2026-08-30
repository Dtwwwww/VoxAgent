from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SpeechModel:
    name: str
    archive_name: str
    url: str
    directory_name: str


SPEECH_MODELS = (
    SpeechModel(
        name="sensevoice-int8",
        archive_name="sensevoice-int8.tar.bz2",
        url="https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
        "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2",
        directory_name="sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17",
    ),
    SpeechModel(
        name="kokoro-int8-zh-en",
        archive_name="kokoro-int8-multi-lang-v1_1.tar.bz2",
        url="https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"
        "kokoro-int8-multi-lang-v1_1.tar.bz2",
        directory_name="kokoro-int8-multi-lang-v1_1",
    ),
    SpeechModel(
        name="melo-zh-en",
        archive_name="vits-melo-tts-zh_en.tar.bz2",
        url="https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"
        "vits-melo-tts-zh_en.tar.bz2",
        directory_name="vits-melo-tts-zh_en",
    ),
)
