from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files


@dataclass(frozen=True, slots=True)
class EmbeddingModelFile:
    name: str
    url: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class EmbeddingModelManifest:
    name: str
    directory: str
    dimension: int
    source: str
    revision: str
    license: str
    files: tuple[EmbeddingModelFile, ...]


def _load_manifest() -> EmbeddingModelManifest:
    resource = files("voxagent.memory").joinpath("embedding_models.json")
    payload = json.loads(resource.read_text(encoding="utf-8"))
    return EmbeddingModelManifest(
        name=payload["Name"],
        directory=payload["Directory"],
        dimension=int(payload["Dimension"]),
        source=payload["Source"],
        revision=payload["Revision"],
        license=payload["License"],
        files=tuple(
            EmbeddingModelFile(
                name=item["Name"],
                url=item["Url"],
                sha256=item["Sha256"],
                size_bytes=int(item["Bytes"]),
            )
            for item in payload["Files"]
        ),
    )


EMBEDDING_MODEL = _load_manifest()
