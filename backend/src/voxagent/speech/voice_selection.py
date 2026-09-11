from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from voxagent.speech.tts import VOICE_REVIEW_SEED, build_voice_review_layout
from voxagent.speech.voice_catalog import VoiceCatalog

APPROVED_VOICE_SAMPLE_ID = "voice-005"
APPROVED_TIED_ENGINE_SAMPLE_IDS = ("engine-002", "engine-003", "engine-004")
APPROVED_ENGINE = "kokoro"
APPROVED_PUBLIC_VOICE: Mapping[str, object] = MappingProxyType(
    {
        "voice_key": "default_voice",
        "display_name": "声灵默认音色",
        "description": "自然清晰，适合日常对话",
        "gender": "neutral",
        "is_default": True,
        "previewable": True,
    }
)
_APPROVED_NATIVE_VOICE_ID = 3
_LISTENER_SCORE_KEYS = frozenset({"naturalness", "intelligibility", "score", "scores"})


class VoiceSelectionError(ValueError):
    pass


def _contains_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int | float):
        return True
    if isinstance(value, Mapping):
        return any(_contains_number(item) for item in value.values())
    if isinstance(value, list | tuple):
        return any(_contains_number(item) for item in value)
    return False


def _contains_numeric_listener_score(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in _LISTENER_SCORE_KEYS and _contains_number(item):
                return True
            if _contains_numeric_listener_score(item):
                return True
    elif isinstance(value, list | tuple):
        return any(_contains_numeric_listener_score(item) for item in value)
    return False


def _validate_sample_entries(
    raw_entries: object,
    expected_ids: tuple[str, ...],
    section: str,
) -> None:
    if not isinstance(raw_entries, list):
        raise VoiceSelectionError(f"{section} must be an array")
    entries: list[Mapping[str, object]] = []
    for entry in raw_entries:
        if not isinstance(entry, Mapping):
            raise VoiceSelectionError(f"{section} entries must be objects")
        entries.append(entry)
    sample_ids = tuple(entry.get("sample_id") for entry in entries)
    if sample_ids != expected_ids:
        raise VoiceSelectionError(f"{section} must contain the exact anonymous sample IDs")
    for entry, sample_id in zip(entries, expected_ids, strict=True):
        if entry.get("file") != f"{sample_id}.wav":
            raise VoiceSelectionError(f"{sample_id} has a mismatched anonymous filename")


def build_approved_voice_artifacts(
    review_template: Mapping[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    if not isinstance(review_template, Mapping):
        raise VoiceSelectionError("review template must be an object")
    schema_version = review_template.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != 1:
        raise VoiceSelectionError("review template schema_version must be 1")
    seed = review_template.get("seed")
    if isinstance(seed, bool) or seed != VOICE_REVIEW_SEED:
        raise VoiceSelectionError(f"review template seed must be {VOICE_REVIEW_SEED}")
    if _contains_numeric_listener_score(review_template):
        raise VoiceSelectionError("review template must not contain numeric listener scores")

    engine_assignments, voice_assignments = build_voice_review_layout()
    engine_ids = tuple(assignment.sample_id for assignment in engine_assignments)
    voice_ids = tuple(assignment.sample_id for assignment in voice_assignments)
    _validate_sample_entries(
        review_template.get("engine_comparison"), engine_ids, "engine_comparison"
    )
    _validate_sample_entries(review_template.get("voice_style"), voice_ids, "voice_style")

    tied_assignments = tuple(
        assignment
        for assignment in engine_assignments
        if assignment.sample_id in APPROVED_TIED_ENGINE_SAMPLE_IDS
    )
    if tuple(assignment.sample_id for assignment in tied_assignments) != (
        APPROVED_TIED_ENGINE_SAMPLE_IDS
    ):
        raise VoiceSelectionError("approved tied engine samples are missing from the seeded layout")
    if not any(assignment.engine == APPROVED_ENGINE for assignment in tied_assignments):
        raise VoiceSelectionError("approved tied engine samples do not include Kokoro")

    selected = tuple(
        assignment
        for assignment in voice_assignments
        if assignment.sample_id == APPROVED_VOICE_SAMPLE_ID
    )
    if len(selected) != 1 or selected[0].native_voice_id != _APPROVED_NATIVE_VOICE_ID:
        raise VoiceSelectionError("voice-005 must resolve to Kokoro native voice 3")
    native_voice_id = selected[0].native_voice_id

    public_voice = dict(APPROVED_PUBLIC_VOICE)
    style = {
        "schema_version": 1,
        "selection_method": "direct_user_choice",
        "review_seed": VOICE_REVIEW_SEED,
        "selected_sample_id": APPROVED_VOICE_SAMPLE_ID,
        "tied_engine_sample_ids": list(APPROVED_TIED_ENGINE_SAMPLE_IDS),
        "selected_engine": APPROVED_ENGINE,
        "engine_tie_break": (
            "Kokoro was retained as the approved tie-break among the tied engine samples."
        ),
        "resolved_native_voice_id": native_voice_id,
        "public_voice": public_voice,
    }
    catalog = {
        "voices": [
            {
                "voice_key": public_voice["voice_key"],
                "display_name": public_voice["display_name"],
                "description": public_voice["description"],
                "gender": public_voice["gender"],
                "engine": APPROVED_ENGINE,
                "native_voice_id": native_voice_id,
                "is_default": public_voice["is_default"],
                "previewable": public_voice["previewable"],
            }
        ]
    }
    return style, catalog


@dataclass(slots=True)
class _PublicationTarget:
    path: Path
    contents: bytes
    existed: bool
    staged: Path | None = None
    backup: Path | None = None
    backup_contains_original: bool = False
    published: bool = False


def _write_staged_file(target: Path, contents: bytes) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".voxagent-tmp", dir=target.parent
    )
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    return staged


def _reserve_backup(target: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".voxagent-backup", dir=target.parent
    )
    os.close(descriptor)
    return Path(name)


def publish_approved_voice_artifacts(
    review_template_path: Path,
    style_output_path: Path,
    catalog_output_path: Path,
) -> None:
    review_path = Path(review_template_path).resolve()
    style_path = Path(style_output_path).resolve()
    catalog_path = Path(catalog_output_path).resolve()
    if len({review_path, style_path, catalog_path}) != 3:
        raise VoiceSelectionError("review template and output paths must be distinct")
    for target in (style_path, catalog_path):
        if target.exists() and not target.is_file():
            raise VoiceSelectionError(f"voice artifact target must be a file: {target}")

    decoded = json.loads(review_path.read_text(encoding="utf-8"))
    if not isinstance(decoded, Mapping):
        raise VoiceSelectionError("review template must be an object")
    style, catalog = build_approved_voice_artifacts(decoded)
    targets = [
        _PublicationTarget(
            style_path,
            (json.dumps(style, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            style_path.exists(),
        ),
        _PublicationTarget(
            catalog_path,
            (json.dumps(catalog, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            catalog_path.exists(),
        ),
    ]

    try:
        for target in targets:
            target.staged = _write_staged_file(target.path, target.contents)
        assert targets[1].staged is not None
        VoiceCatalog.load(targets[1].staged)
        for target in targets:
            if target.existed:
                target.backup = _reserve_backup(target.path)
        for target in targets:
            if target.backup is not None:
                os.replace(target.path, target.backup)
                target.backup_contains_original = True
        for target in targets:
            assert target.staged is not None
            os.replace(target.staged, target.path)
            target.staged = None
            target.published = True
    except BaseException as error:
        rollback_errors: list[OSError] = []
        for target in reversed(targets):
            try:
                if target.backup_contains_original:
                    assert target.backup is not None
                    os.replace(target.backup, target.path)
                    target.backup_contains_original = False
                elif not target.existed and target.published:
                    target.path.unlink(missing_ok=True)
            except OSError as rollback_error:
                rollback_errors.append(rollback_error)
        if rollback_errors:
            raise OSError("voice artifact publication rollback failed") from error
        raise
    else:
        for target in targets:
            if target.backup is not None:
                target.backup.unlink(missing_ok=True)
                target.backup = None
    finally:
        for target in targets:
            if target.staged is not None:
                target.staged.unlink(missing_ok=True)
            if target.backup is not None and not target.backup_contains_original:
                target.backup.unlink(missing_ok=True)
