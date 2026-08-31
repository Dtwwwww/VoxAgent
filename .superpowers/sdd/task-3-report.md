# Task 3 report: Offline ASR and measured speech baseline

## RED evidence

Before production ASR or benchmark code existed, ran:

```powershell
cd backend
uv run --extra dev --extra speech pytest tests/speech/test_asr.py tests/diagnostics/test_speech_benchmark.py tests/test_cli.py -v
```

Collection failed as expected because `voxagent.speech.asr` did not exist and
`FixtureChecksumError` was not exported from the benchmark module. A later
candidate-pause test also failed as expected because
`SenseVoiceCandidatePauseAsr` did not exist. The Paraformer manifest/downloader
tests then failed as expected: the inventory had four rather than five entries,
`SpeechModel` had no `archive_size_bytes`, and the downloader accepted a wrong
declared archive size.

## GREEN evidence

Implemented a test-injectable CPU-only SenseVoice adapter, an online Paraformer
adapter that accepts only new 20 ms frames and emits public updates no more than
once per 500 ms, and a candidate-pause SenseVoice probe for benchmark selection.
SenseVoice removes only known `<|...|>` control tags while retaining punctuation
and ordinary angle-bracket text; language, emotion, and sound labels are
returned solely as metadata.

The benchmark validates `benchmarks/fixtures/checksums.json` before any model
load, runs one warm-up plus five measured passes, records final and partial
latency percentiles, audio duration, RTF, transcript, RSS, and model IDs, then
updates `asr_candidates` only from a written measured artifact. It selects
SenseVoice candidate pauses only when p95 is at most 300 ms; otherwise it loads
the streaming Paraformer fallback.

Focused verification:

```text
45 passed in 21.06s
All checks passed!
uv lock --check: exit 0
```

Full backend coverage was run in three disjoint groups to keep the Windows
terminal invocation below its output-time limit:

```text
68 passed in 2.81s
53 passed, 1 skipped in 23.72s
15 passed in 7.68s
```

The total is 136 passed and one explicitly skipped opt-in local VAD smoke.
`git diff --check` exited 0.

## Runtime smoke

Used the installed model directory:

```text
D:\VoxAgentData\models\speech\sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17
```

The real production initialization completed successfully:

```text
SENSEVOICE_INIT_OK
SENSEVOICE_INIT_EXIT_CODE=0
```

The adapter calls the Task 2 `prepare_sherpa_onnx_runtime()` before every
production `sherpa_onnx` import; no System32 DLL was changed.

## Paraformer manifest provenance

Pinned the exact official release URL:

```text
https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-streaming-paraformer-bilingual-zh-en.tar.bz2
```

The official GitHub release API exposed the file size but no digest. The
controller therefore performed one complete download from that exact URL on
2026-08-31, checked that its 1,047,319,737 bytes match the release metadata,
and locally calculated the pinned SHA-256:

```text
5462a1fce42693deae572af1e8c4687124b12aa85fe61ff4d3168bb5280e205f
```

The standard downloader now verifies both the optional manifest byte count and
the SHA-256 before extraction. No duplicate download was performed here.

## Fixture blocker

`benchmarks/fixtures/mandarin-command.wav` and its committed
`benchmarks/fixtures/checksums.json` are absent. No audio was synthesized,
downloaded, substituted, or benchmarked. The required command honestly exits
before any model load or output write:

```text
BENCHMARK_EXIT_CODE=2
BENCHMARK_ARTIFACT_EXISTED_BEFORE=False
BENCHMARK_ARTIFACT_EXISTS_AFTER=False
```

After a user-recorded, non-sensitive 10–20 second Mandarin file and matching
checksum manifest are committed, run exactly:

```powershell
cd D:\Agent_protect\VoxAgent（声灵）\.worktrees\phase-02-voice-loop\backend
uv run --extra speech voxagent benchmark-asr --wav ..\benchmarks\fixtures\mandarin-command.wav --output ..\benchmarks\sensevoice-int8.json
```

That command is the only path that may create the measured artifact and update
the baseline's `asr_candidates` entry.
