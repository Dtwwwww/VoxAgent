import json

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


def test_production_catalog_exposes_only_intelligible_local_fallback():
    catalog = load_production_catalog()
    profiles = catalog.public_profiles()

    assert [item.voice_key for item in profiles] == ["default_voice", "breezy_tw_female"]
    assert profiles[0].is_default is True
    assert not hasattr(profiles[0], "engine")
    assert not hasattr(profiles[0], "native_voice_id")
    internal = catalog.get("default_voice")
    assert (internal.engine, internal.native_voice_id) == ("kokoro", 3)
    breezy = catalog.get("breezy_tw_female")
    assert (breezy.engine, breezy.native_voice_id) == ("breezyvoice", 0)
