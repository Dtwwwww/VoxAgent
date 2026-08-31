# Task 4 report: Local TTS, sentence streaming, and voice review

## Status

`NEEDS_USER_VOICE_REVIEW`. The implementation, test coverage, real local
Kokoro/Melo synthesis, all required benchmark artifacts, and anonymous review
package are complete. No production `voice_catalog.json`,
`tts-voice-style.json`, or selected-TTS baseline entry has been fabricated:
those require listener scores.

## RED / GREEN evidence

The first focused RED run failed at collection exactly because the new modules
did not exist:

```text
ModuleNotFoundError: voxagent.conversation.sentence_chunker
ModuleNotFoundError: voxagent.speech.voice_catalog
ModuleNotFoundError: voxagent.speech.tts
```

After the minimal chunker, strict immutable catalog, and CPU-only Sherpa TTS
adapter were implemented, the focused suite passed. Separate RED cycles then
proved missing TTS benchmark publication, blind-review generation, CLI commands,
and production-catalog gate before their implementations were added.

Latest focused verification:

```text
53 passed
All checks passed!
uv lock --check: exit 0
```

## Implementation notes

- `SentenceChunker` emits on terminal punctuation, chooses commas only in the
  12--30-character window, hard-splits long unpunctuated deltas, and flushes
  its tail exactly once.
- Catalog records are strict and server-owned. Public profiles expose only
  `voice_key`, presentation text, gender, default, and previewable flags.
  Native engine/ID/path values do not cross the public boundary.
- `SherpaOfflineTts` calls Task 2's Windows ONNX Runtime preparation before
  importing Sherpa, forces CPU/one thread, validates exact public speeds
  `0.8`, `1.0`, `1.2`, resolves native IDs only through the catalog, and writes
  in-memory mono PCM16 WAV at the model's native sample rate.
- The shared TTS benchmark performs one warm-up plus five measured calls per
  fixed text and retains the maximum sampled process RSS. Its dual-file
  publication delegates to the already-tested transactional publisher.

## Real local evidence

Commands were run against the installed official directories with no model
asset modifications:

```powershell
uv run --extra speech voxagent benchmark-tts --engine kokoro --voice-id 3 --output ..\benchmarks\kokoro-int8.json
uv run --extra speech voxagent benchmark-tts --engine melo --voice-id 0 --output ..\benchmarks\melo-zh-en.json
uv run --extra speech voxagent prepare-voice-review --output-dir ..\benchmarks\voice-review
```

`benchmarks/kokoro-int8.json` records non-empty 24 kHz synthesis for all three
fixed texts (voice 3), with a real peak RSS of `432910336` bytes. Its p50/p95
first-audio latencies are 12.254/14.331 s, 11.632/12.813 s, and 11.334/12.728 s.
The remaining required Kokoro native IDs were measured in the same 1+5-by-three
fixed-text protocol and retained as
`kokoro-int8-voice-13.json`, `kokoro-int8-voice-27.json`,
`kokoro-int8-voice-43.json`, `kokoro-int8-voice-58.json`,
`kokoro-int8-voice-69.json`, `kokoro-int8-voice-85.json`, and
`kokoro-int8-voice-98.json`.
`benchmarks/melo-zh-en.json` records non-empty 44.1 kHz synthesis (voice 0),
with a real peak RSS of `416681984` bytes; its corresponding p50/p95 latencies
are 1.529/1.658 s, 1.853/2.235 s, and 1.991/2.115 s.

Sherpa printed one native `Unknown token: ?` initialization diagnostic for
Kokoro, but all three fixed Chinese/English texts generated valid, non-empty
mono PCM16 WAV output. The adapter does not suppress that engine diagnostic.

The ignored `benchmarks/voice-review/` package contains six anonymous engine
comparison WAVs, eight anonymous candidate WAVs, and `review-template.json`.
Readback verified every WAV is mono PCM16; Kokoro samples are 24 kHz and Melo
samples are 44.1 kHz. The listener-visible template has seed `20260830`, no
engine/native-ID/path fields, 1--5 naturalness/intelligibility scoring, unique
assignment rule, and a selected-score minimum of 3.

## Blocker / required user review

The user must fill `benchmarks/voice-review/review-template.json` after
listening blind. For every anonymous sample, provide naturalness and
intelligibility in `[1, 5]`; for four distinct Kokoro candidates assign exactly
one each of `清澈女声`, `温柔女声`, `沉稳男声`, and `阳光男声`. Every selected score must
be at least 3. Only then may a controller resolve the private deterministic
assignment, create the exact four-record production catalog
(`clear_female`, `warm_female`, `steady_male`, `bright_male`), write
`tts-voice-style.json`, and transactionally append the selected TTS candidate
to the baseline.

## Independent review remediation

### RED evidence

The review found that an exactly 30-character unpunctuated streamed delta was
not emitted because the hard-split comparison was strict (`> 30`). The new
single-case regression first failed with `feed("x" * 30) == ()`.

It also established that review WAVs were written without parsing or duration
validation. Two real preview files exceeded the stated five-second limit. New
tests first failed because the review-normalization helper did not exist; they
cover durations 2.999, 3, 5, and 5.001 seconds, invalid stereo/PCM24/native
rate mismatch, and refusal to cut through a speaking tail. The factory-path
tests likewise first failed because the missing helper/contract was absent.

### GREEN evidence

- The chunker now hard-splits at `>= 30`; terminal punctuation, safe comma,
  mixed-text, and flush regressions remain covered.
- Review generation uses the public fixed speed 1.2. It parses every returned
  WAV and requires a readable mono PCM16 WAV with matching positive native
  sample rate. It safely pads sub-three-second outputs with silence, removes
  only a tail that is verified silent after five seconds, and fails explicitly
  rather than truncating spoken audio at the boundary.
- `from_model_dir()` checks the caller-supplied path before resolving it and
  reports every absent model asset as an absolute `ModelAssetError` path.

The regenerated ignored review package was read back after synthesis: all 14
WAVs are mono PCM16, retain their native 24 kHz (Kokoro) or 44.1 kHz (Melo)
rate, and have durations within 3.0--5.0 seconds. The measured duration range
is 3.000000--4.177917 seconds. The `review-template.json` seed remains
`20260830` and remains anonymous.

Focused remediation verification:

```text
64 passed
All checks passed!
```
