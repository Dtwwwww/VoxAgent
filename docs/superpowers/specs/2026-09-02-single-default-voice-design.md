# VoxAgent Single Default Voice Design

Date: 2026-09-02
Status: user-approved design amendment
Supersedes: the four-voice blind-scoring requirement in Plan 2 Task 4 and the corresponding
four-voice completion checks in Tasks 8–9.

## Context

Task 4 generated six anonymous engine-comparison samples and eight anonymous Kokoro
voice-style samples. The user directly selected `voice-005` and rated `engine-002`,
`engine-003`, and `engine-004` as tied candidates. The approved tie-break keeps Kokoro, which
was already the preferred selectable engine unless it failed a fixed Mandarin synthesis case.

This direct selection replaces numeric blind scoring. The project must record what the user
actually chose and must not invent naturalness or intelligibility scores.

## Product Decision

The first usable version exposes exactly one public voice:

- review sample: `voice-005`;
- engine: Kokoro;
- public `voice_key`: `default_voice`;
- display name: `声灵默认音色`;
- description: `自然清晰，适合日常对话`;
- gender: `neutral`;
- `is_default`: `true`;
- `previewable`: `true`;
- public speeds: exactly `0.8`, `1.0`, and `1.2`.

The deterministic review seed `20260830` resolves `voice-005` to its private Kokoro native
speaker ID during implementation. That ID, the anonymous sample name, model paths, and engine
internals never cross the WebSocket boundary and never enter browser storage.

## Artifacts and Data Flow

`benchmarks/tts-voice-style.json` records selection provenance without fabricated scores:

- selection method `direct_user_choice`;
- selected sample `voice-005`;
- tied engine samples `engine-002`, `engine-003`, and `engine-004`;
- selected engine `kokoro` and the documented tie-break;
- deterministic seed and the resolved private native ID.

`backend/src/voxagent/speech/voice_catalog.json` contains one validated internal voice record.
The server publishes only its public fields through `voices.available`. Task 7 already accepts
and maintains server-driven voice data, so a one-entry catalog requires no protocol rename.
Task 8 renders one voice card and retains the three exact speed choices. `voxagent serve`
continues to fail closed until the production catalog exists and validates.

## Validation and Error Handling

- Recompute the seed-based anonymous mapping and prove `voice-005` resolves to the catalog's
  native ID; do not hard-code an unverified guess.
- Reject selection artifacts that contain invented numeric listener scores, a different sample,
  a non-Kokoro engine, extra public voices, or private fields in the public payload.
- Preserve all existing catalog startup validation, public-field filtering, safe local storage,
  preview pairing, and native-rate WAV behavior.
- If the selected private speaker cannot synthesize any fixed Mandarin benchmark sentence,
  fail clearly and return to user selection instead of silently substituting another voice.

## Testing and Acceptance

Automated tests must prove:

- deterministic unblinding maps `voice-005` to exactly one private Kokoro speaker;
- the production catalog has exactly one default, previewable public entry;
- `voices.available` exposes `default_voice` and no anonymous/native/private identifiers;
- selection provenance contains no fabricated listener score;
- preview and spoken replies work at `0.8`, `1.0`, and `1.2` using the public key.

Manual acceptance replaces the former four-voice exercise with one selected-voice exercise:
preview and answer once at each speed, refresh to confirm only the public voice key and speed
persist, and confirm neither `voice-005` nor the native speaker ID appears in the browser.

The user approved a first-version latency amendment after reviewing the measured Kokoro ID 3
benchmark (`14.331` seconds p95 TTS first-audio latency). Keep the selected voice and change only
the p95 `first assistant text token -> first TTS audio chunk` acceptance ceiling from `4.0` to
`15.0` seconds. The `natural=1.35s` endpoint profile, the approximately `2.0s` incomplete-ending
extension, and the `200ms` barge-in playback-stop target remain unchanged. Record the measured
value rather than claiming that Kokoro meets the former four-second gate.

## Out of Scope

The first version does not create placeholder female/male voices, duplicate `voice-005` under
multiple labels, or automatically choose three additional voices. Additional curated voices
require a later user-approved selection and a versioned catalog update.
