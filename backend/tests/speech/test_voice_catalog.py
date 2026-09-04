import json
from pathlib import Path

import pytest

from voxagent.speech.voice_catalog import VoiceCatalog, VoiceCatalogError, load_production_catalog


def _voice(
    *, key: str = "clear_female", default: bool = True, **overrides: object
) -> dict[str, object]:
    value: dict[str, object] = {
        "voice_key": key,
        "display_name": "清澈女声",
        "description": "明亮清晰",
        "gender": "female",
        "engine": "kokoro",
        "native_voice_id": 3,
        "is_default": default,
        "previewable": True,
    }
    value.update(overrides)
    return value


def _load(tmp_path, voices: list[dict[str, object]]) -> VoiceCatalog:
    path = tmp_path / "voices.json"
    path.write_text(json.dumps({"voices": voices}, ensure_ascii=False), encoding="utf-8")
    return VoiceCatalog.load(path)


def test_catalog_returns_a_public_profile_without_server_identifiers(tmp_path):
    catalog = _load(tmp_path, [_voice()])

    public = catalog.public_profiles()

    assert public[0].voice_key == "clear_female"
    assert not hasattr(public[0], "engine")
    assert not hasattr(public[0], "native_voice_id")
    assert catalog.get("clear_female").native_voice_id == 3


@pytest.mark.parametrize(
    ("voices", "message"),
    [
        ([_voice(), _voice(key="clear_female", default=False)], "duplicate voice_key"),
        ([_voice(), _voice(key="warm_female", default=False)], "duplicate display_name"),
        ([_voice(default=False)], "exactly one default"),
        (
            [_voice(), _voice(key="warm_female", default=True, display_name="温柔女声")],
            "exactly one default",
        ),
        ([_voice(engine="cloud")], "unknown engine"),
        ([_voice(native_voice_id=-1)], "native_voice_id"),
        ([{**_voice(), "path": "D:/secret"}], "unexpected fields"),
    ],
)
def test_catalog_rejects_invalid_server_owned_records(tmp_path, voices, message):
    with pytest.raises(VoiceCatalogError, match=message):
        _load(tmp_path, voices)


def test_catalog_rejects_unknown_voice_key(tmp_path):
    catalog = _load(tmp_path, [_voice()])

    with pytest.raises(VoiceCatalogError, match="Unknown voice_key"):
        catalog.get("browser-native-id-3")


def test_production_catalog_matches_the_approved_voice_artifact():
    catalog = load_production_catalog()
    profile = catalog.get("default_voice")
    style_path = Path(__file__).resolve().parents[3] / "benchmarks" / "tts-voice-style.json"
    style = json.loads(style_path.read_text(encoding="utf-8"))

    assert style["selected_sample_id"] == "voice-005"
    assert style["tied_engine_sample_ids"] == ["engine-002", "engine-003", "engine-004"]
    assert style["selected_engine"] == profile.engine == "kokoro"
    assert style["resolved_native_voice_id"] == profile.native_voice_id == 3
    assert style["public_voice"] == {
        "voice_key": "default_voice",
        "display_name": "声灵默认音色",
        "description": "自然清晰，适合日常对话",
        "gender": "neutral",
        "is_default": True,
        "previewable": True,
    }
