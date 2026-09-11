import json
from dataclasses import dataclass
from importlib.resources import files


@dataclass(frozen=True, slots=True)
class SpeechModel:
    name: str
    format: str
    archive_name: str
    url: str
    directory_name: str
    version: str
    source: str
    archive_sha256: str
    required_files: tuple[str, ...]
    archive_size_bytes: int | None = None


def _load_models() -> tuple[SpeechModel, ...]:
    manifest = files("voxagent.speech").joinpath("models.json")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    return tuple(
        SpeechModel(
            name=item["Name"],
            format=item.get("Format", "archive"),
            archive_name=item["Archive"],
            url=item["Url"],
            directory_name=item["Directory"],
            version=item["Version"],
            source=item["Source"],
            archive_sha256=item["ArchiveSha256"],
            required_files=tuple(item["RequiredFiles"]),
            archive_size_bytes=item.get("ArchiveBytes"),
        )
        for item in payload
    )


SPEECH_MODELS = _load_models()
