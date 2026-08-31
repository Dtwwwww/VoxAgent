from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import as_file, files
from pathlib import Path
from typing import Literal

VoiceGender = Literal["female", "male", "neutral"]
VoiceEngine = Literal["kokoro", "melo"]
_VOICE_FIELDS = frozenset(
    {
        "voice_key",
        "display_name",
        "description",
        "gender",
        "engine",
        "native_voice_id",
        "is_default",
        "previewable",
    }
)


class VoiceCatalogError(ValueError):
    """A server-owned voice catalog is invalid or a public key is unknown."""


@dataclass(frozen=True, slots=True)
class VoiceProfile:
    voice_key: str
    display_name: str
    description: str
    gender: VoiceGender
    engine: VoiceEngine
    native_voice_id: int
    is_default: bool
    previewable: bool


@dataclass(frozen=True, slots=True)
class PublicVoiceProfile:
    voice_key: str
    display_name: str
    description: str
    gender: VoiceGender
    is_default: bool
    previewable: bool


@dataclass(frozen=True, slots=True)
class VoiceCatalog:
    _profiles: tuple[VoiceProfile, ...]

    @classmethod
    def load(cls, path: Path) -> VoiceCatalog:
        try:
            decoded = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise VoiceCatalogError(f"Unable to load voice catalog: {path}") from error
        if not isinstance(decoded, dict) or set(decoded) != {"voices"}:
            raise VoiceCatalogError("voice catalog must contain only a voices array")
        records = decoded["voices"]
        if not isinstance(records, list) or not records:
            raise VoiceCatalogError("voice catalog must contain at least one voice")
        profiles = tuple(cls._profile_from_record(record) for record in records)
        cls._validate_profiles(profiles)
        return cls(profiles)

    @staticmethod
    def _profile_from_record(record: object) -> VoiceProfile:
        if not isinstance(record, dict):
            raise VoiceCatalogError("voice record must be an object")
        fields = set(record)
        if fields != _VOICE_FIELDS:
            raise VoiceCatalogError("voice record has missing or unexpected fields")
        string_fields = ("voice_key", "display_name", "description")
        if any(
            not isinstance(record[field], str) or not record[field].strip()
            for field in string_fields
        ):
            raise VoiceCatalogError("voice text fields must be non-empty strings")
        gender = record["gender"]
        engine = record["engine"]
        native_voice_id = record["native_voice_id"]
        if gender not in {"female", "male", "neutral"}:
            raise VoiceCatalogError("unknown gender")
        if engine not in {"kokoro", "melo"}:
            raise VoiceCatalogError("unknown engine")
        if (
            isinstance(native_voice_id, bool)
            or not isinstance(native_voice_id, int)
            or native_voice_id < 0
        ):
            raise VoiceCatalogError("native_voice_id must be a non-negative integer")
        if not isinstance(record["is_default"], bool) or not isinstance(
            record["previewable"], bool
        ):
            raise VoiceCatalogError("voice flags must be booleans")
        return VoiceProfile(
            voice_key=record["voice_key"],
            display_name=record["display_name"],
            description=record["description"],
            gender=gender,
            engine=engine,
            native_voice_id=native_voice_id,
            is_default=record["is_default"],
            previewable=record["previewable"],
        )

    @staticmethod
    def _validate_profiles(profiles: tuple[VoiceProfile, ...]) -> None:
        if len({profile.voice_key for profile in profiles}) != len(profiles):
            raise VoiceCatalogError("duplicate voice_key")
        if len({profile.display_name for profile in profiles}) != len(profiles):
            raise VoiceCatalogError("duplicate display_name")
        if sum(profile.is_default for profile in profiles) != 1:
            raise VoiceCatalogError("voice catalog requires exactly one default")

    def get(self, voice_key: str) -> VoiceProfile:
        for profile in self._profiles:
            if profile.voice_key == voice_key:
                return profile
        raise VoiceCatalogError(f"Unknown voice_key: {voice_key}")

    def public_profiles(self) -> tuple[PublicVoiceProfile, ...]:
        return tuple(
            PublicVoiceProfile(
                voice_key=profile.voice_key,
                display_name=profile.display_name,
                description=profile.description,
                gender=profile.gender,
                is_default=profile.is_default,
                previewable=profile.previewable,
            )
            for profile in self._profiles
        )


def load_production_catalog() -> VoiceCatalog:
    """Load the immutable approved catalog, never synthesizing a guessed default."""
    resource = files("voxagent.speech").joinpath("voice_catalog.json")
    if not resource.is_file():
        raise VoiceCatalogError("Production voice catalog requires scored blind voice review")
    with as_file(resource) as path:
        return VoiceCatalog.load(path)
