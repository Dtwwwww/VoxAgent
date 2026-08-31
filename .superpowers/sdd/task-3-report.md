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

## Controller Paraformer production verification

The controller placed the verified archive under the standard speech-model
root and ran the selected-model downloader. The first run validated and
extracted it through staging; the second run reported:

```text
Present: streaming-paraformer-bilingual-zh-en
```

The published directory contains `encoder.int8.onnx`, `decoder.int8.onnx`,
`tokens.txt`, and a completion marker. Real production initialization then
completed with:

```text
PARAFORMER_INIT_OK
PARAFORMER_INIT_EXIT_CODE=0
```

## Benchmark-path repair and measured baseline

### RED and root cause

The first real benchmark reached the installed streaming Paraformer then failed
at `StreamingParaformerAsr.accept()` with:

```text
AttributeError: 'str' object has no attribute 'text'
```

The pinned `sherpa-onnx==1.13.6` online wrapper documents
`OnlineRecognizer.get_result()` as a plain string. The unit fake had returned
an object with `.text`, masking that contract. New regression tests changed
the fake to the real string contract and reproduced the failure before the
production adapter was changed.

The same RED run exposed the independent benchmark defects: relative default
baseline resolution from `backend`, acceptance of <10s and >20s fixtures,
three-decimal timing rounding at the 300ms selection threshold, a terminal RSS
sample rather than a peak, malformed non-hex checksum acceptance, and a
candidate report plus full report that repeated final ASR work.

### GREEN changes

- Streaming Paraformer now consumes the pinned wrapper's returned `str`.
- Benchmark timing retains full precision until JSON rendering, so 300.4ms
  correctly selects Paraformer rather than SenseVoice.
- Fixture SHA-256 values require exactly 64 hexadecimal characters, and WAV
  duration is constrained to 10–20 seconds before frame validation.
- Candidate partial probing is separate from the one formal final benchmark;
  both SenseVoice-selected and Paraformer-fallback paths make one final
  warm-up plus five measured final calls.
- RSS is sampled after partial reset/work and every final operation, retaining
  the maximum.
- The default baseline is anchored at the repository root. Baseline parsing
  and artifact/baseline payload construction occur before artifact creation.
  The artifact is written as the exact UTF-8 bytes that were hashed for the
  baseline, preventing CRLF-related hash drift.

Focused GREEN verification:

```text
33 passed in 1.50s
All checks passed!
```

### User-authorized fixture and real benchmark

The final user-recorded non-sensitive fixture was used without modification:

```text
Path: benchmarks/fixtures/mandarin-command.wav
Format: 16 kHz mono PCM16 WAV
Duration: 15.0 seconds
Bytes: 480044
SHA-256: 0c087907bfdd8296acd9c5c5d34e28e8081955448dfc7c6bb46fa33647415221
```

The exact planned command was rerun after the artifact-byte fix:

```powershell
cd D:\Agent_protect\VoxAgent（声灵）\.worktrees\phase-02-voice-loop\backend
uv run --extra speech voxagent benchmark-asr --wav ..\benchmarks\fixtures\mandarin-command.wav --output ..\benchmarks\sensevoice-int8.json
```

Measured artifact `benchmarks/sensevoice-int8.json`:

```text
Model: sensevoice-int8
Partial model: streaming-paraformer-bilingual-zh-en
Final ASR: p50 0.890s, p95 1.095s
Partial update: p50 0.086s, p95 0.127s
RTF: p50 0.059, p95 0.073
Peak RSS: 1038774272 bytes
Artifact SHA-256: fc5ca2c98fb111f8aabad5d5e0c65128c24638b1b022330c764d2fe8d78d8dfe
```

The artifact is non-empty and `target-machine-baseline.json` now has a matching
non-empty `asr_candidates` entry with the same artifact SHA-256. The transcript
is `你好，森林，请检查本地模型，并告诉我今天的日期。`; it preserves the command
intent and full request but recognizes the name `声灵` phonetically as `森林`.

## Atomic benchmark publication repair

### RED

Code review correctly identified that the CLI wrote the measured artifact
before the baseline. A baseline ACL, disk, or I/O failure could therefore
leave the new artifact next to the old baseline. A new injected-replace
regression test first failed at import because the dual-file transaction did
not exist. The test injects an `OSError` precisely on the second publication
replacement (the temporary baseline file to the baseline target), then checks
the byte-for-byte pre-call state for four cases: both targets existed, either
target was absent, and both targets were absent.

An added success-path cleanup test also initially failed: successful
publication left the old `.voxagent-backup` files behind. This exposed a real
completion-path defect before the final cleanup change.

### GREEN

`publish_asr_benchmark()` now writes each complete payload to a unique,
same-directory `.voxagent-tmp` file and calls `flush()` plus `fsync()` before
any target changes. Existing targets are atomically moved to unique,
same-directory `.voxagent-backup` files; the artifact and baseline are then
published with `os.replace()`. If either publication replacement fails, the
owned backups restore the original bytes, and a newly-created target that did
not previously exist is removed. Only explicitly-created temporary/backup
paths and the controlled caller-supplied newly-created target are removed.
Successful publication removes all owned temporary and backup files.

Focused verification after the final cleanup repair:

```text
27 passed in 1.37s
All checks passed!
```

No user fixture, measured artifact, or baseline metric was regenerated or
changed during this repair.
