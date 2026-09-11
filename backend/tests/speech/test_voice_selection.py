from copy import deepcopy
from dataclasses import replace

import pytest

from voxagent.speech import voice_selection
from voxagent.speech.tts import TTS_BENCHMARK_TEXTS, build_voice_review_layout
from voxagent.speech.voice_selection import (
    APPROVED_PUBLIC_VOICE,
    VoiceSelectionError,
    build_approved_voice_artifacts,
)


def _valid_review_template() -> dict[str, object]:
    return {
        "schema_version": 1,
        "seed": 20260830,
        "engine_comparison": [
            {
                "sample_id": f"engine-{index:03d}",
                "file": f"engine-{index:03d}.wav",
                "naturalness": None,
                "intelligibility": None,
            }
            for index in range(1, 7)
        ],
        "voice_style": [
            {
                "sample_id": f"voice-{index:03d}",
                "file": f"voice-{index:03d}.wav",
                "assigned_label": None,
                "naturalness": None,
                "intelligibility": None,
            }
            for index in range(1, 9)
        ],
        "required_voice_labels": ["清澈女声", "温柔女声", "沉稳男声", "阳光男声"],
        "scoring_rules": {
            "range": [1, 5],
            "minimum_selected_score": 3,
            "no_duplicate_assignments": True,
        },
    }


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _all_keys(item)}
    if isinstance(value, list):
        return {key for item in value for key in _all_keys(item)}
    return set()


def test_voice_review_layout_is_deterministic_and_preserves_shuffle_coupling():
    first = build_voice_review_layout()
    second = build_voice_review_layout()

    assert first == second
    engines, voices = first
    assert [
        (item.sample_id, item.engine, item.native_voice_id, item.text) for item in engines
    ] == [
        ("engine-001", "kokoro", 3, TTS_BENCHMARK_TEXTS[2]),
        ("engine-002", "kokoro", 3, TTS_BENCHMARK_TEXTS[1]),
        ("engine-003", "melo", 0, TTS_BENCHMARK_TEXTS[2]),
        ("engine-004", "melo", 0, TTS_BENCHMARK_TEXTS[1]),
        ("engine-005", "melo", 0, TTS_BENCHMARK_TEXTS[0]),
        ("engine-006", "kokoro", 3, TTS_BENCHMARK_TEXTS[0]),
    ]
    assert [(item.sample_id, item.native_voice_id) for item in voices] == [
        ("voice-001", 85),
        ("voice-002", 69),
        ("voice-003", 43),
        ("voice-004", 13),
        ("voice-005", 3),
        ("voice-006", 27),
        ("voice-007", 98),
        ("voice-008", 58),
    ]


def test_approved_voice_artifacts_record_direct_choice_without_fabricated_scores():
    style, catalog = build_approved_voice_artifacts(_valid_review_template())

    assert catalog == {
        "voices": [
            {
                "voice_key": "default_voice",
                "display_name": "声灵默认音色",
                "description": "自然清晰，适合日常对话",
                "gender": "neutral",
                "engine": "kokoro",
                "native_voice_id": 3,
                "is_default": True,
                "previewable": True,
            }
        ]
    }
    assert style["schema_version"] == 1
    assert style["selection_method"] == "direct_user_choice"
    assert style["review_seed"] == 20260830
    assert style["selected_sample_id"] == "voice-005"
    assert style["tied_engine_sample_ids"] == [
        "engine-002",
        "engine-003",
        "engine-004",
    ]
    assert style["selected_engine"] == "kokoro"
    assert isinstance(style["engine_tie_break"], str)
    assert style["engine_tie_break"].strip()
    assert style["resolved_native_voice_id"] == 3
    assert style["public_voice"] == dict(APPROVED_PUBLIC_VOICE)
    assert not {
        "naturalness",
        "intelligibility",
        "score",
        "scores",
    } & _all_keys(style)


@pytest.mark.parametrize(
    "invalid_case",
    [
        "wrong_seed",
        "missing_selected_voice",
        "duplicate_selected_voice",
        "missing_tied_engine",
        "mismatched_filename",
        "numeric_score",
        "selected_voice_resolves_elsewhere",
    ],
)
def test_approved_voice_artifacts_reject_invalid_or_unverifiable_review_templates(
    invalid_case, monkeypatch
):
    template = deepcopy(_valid_review_template())
    if invalid_case == "wrong_seed":
        template["seed"] = 7
    elif invalid_case == "missing_selected_voice":
        template["voice_style"] = [
            item for item in template["voice_style"] if item["sample_id"] != "voice-005"
        ]
    elif invalid_case == "duplicate_selected_voice":
        selected = next(
            item for item in template["voice_style"] if item["sample_id"] == "voice-005"
        )
        template["voice_style"].append(deepcopy(selected))
    elif invalid_case == "missing_tied_engine":
        template["engine_comparison"] = [
            item
            for item in template["engine_comparison"]
            if item["sample_id"] != "engine-003"
        ]
    elif invalid_case == "mismatched_filename":
        template["engine_comparison"][0]["file"] = "engine-006.wav"
    elif invalid_case == "numeric_score":
        template["voice_style"][0]["naturalness"] = 5
    else:
        engines, voices = build_voice_review_layout()
        mismatched = tuple(
            replace(item, native_voice_id=13) if item.sample_id == "voice-005" else item
            for item in voices
        )
        monkeypatch.setattr(
            voice_selection,
            "build_voice_review_layout",
            lambda: (engines, mismatched),
        )

    with pytest.raises(VoiceSelectionError):
        build_approved_voice_artifacts(template)
