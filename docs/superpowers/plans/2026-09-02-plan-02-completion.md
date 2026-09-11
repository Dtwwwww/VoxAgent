# Plan 2 Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish Plan 2 as a locally runnable Web application with shared text/voice conversation, the user-selected `voice-005` Kokoro voice, safe public voice settings, and reproducible automated/manual acceptance evidence.

**Architecture:** Preserve the existing localhost-only FastAPI WebSocket orchestration and React session controller. Add one deterministic server-side unblinding/finalization path that produces the immutable production voice catalog, then build the Web presentation layer on the already-tested controller. Keep all private voice IDs and model paths in the backend. Run the Vite Web client separately from the backend during Plan 2 development; both connect only over loopback.

**Tech Stack:** Python 3.12, FastAPI, Typer, pytest, sherpa-onnx, Ollama, React 19, TypeScript 7, Vite 8, Vitest, Testing Library, pnpm, PowerShell.

## Global Constraints

- Work only in the existing linked worktree `D:\Agent_protect\VoxAgent（声灵）\.worktrees\phase-02-voice-loop` on branch `phase/02-voice-loop`.
- Follow red-green-refactor for every production behavior: add the focused failing test, run it and record the expected failure, implement the minimum behavior, then rerun the focused and related tests.
- The application binds only to `127.0.0.1`; Ollama calls use `http://127.0.0.1:11434` with environment proxy inheritance disabled. No cloud API is introduced.
- The approved review sample is exactly `voice-005`; deterministic seed `20260830` must resolve it to Kokoro native speaker ID `3`. Recompute that mapping from the same seeded shuffle used to generate the review files; do not merely type `3` into the catalog without verification.
- Engine samples `engine-002`, `engine-003`, and `engine-004` are recorded as tied. The approved tie-break selects Kokoro. Do not invent naturalness or intelligibility scores.
- Expose exactly one public voice: `voice_key=default_voice`, display name `声灵默认音色`, description `自然清晰，适合日常对话`, gender `neutral`, `is_default=true`, and `previewable=true`.
- Public voice speeds are exactly `0.8`, `1.0`, and `1.2`. Browser messages and storage must never contain `voice-005`, native speaker ID `3`, engine names, model paths, audio, transcript history, or the session token.
- Agent messages render on the left as `Agent（声灵）`; user messages render on the right as `用户`.
- Text and voice inputs share the existing in-memory conversation history. Text submission remains usable while microphone capture is active and after microphone permission denial.
- Endpoint profiles remain `fast=0.8s`, `natural=1.35s`, and `patient=2.0s`; `natural` is the default and incomplete Mandarin endings/fillers extend it to approximately `2.0s`.
- Barge-in must stop browser playback within `200ms` and stale audio must never resume.
- The first-version latency gate is p95 `first assistant text token -> first TTS audio chunk <= 15.0s`. This replaces only the former `4.0s` TTS gate because the approved Kokoro ID 3 benchmark measured `14.331s` p95. Do not weaken any endpoint, cancellation, privacy, or text-response requirement.
- Raw microphone audio remains signed little-endian PCM16, mono, 16kHz, fixed 20ms frames (320 samples / 640 bytes). Server TTS remains native-rate mono PCM16 WAV.
- Generated review WAVs remain ignored. Only deterministic JSON provenance, catalog data, tests, source, and sanitized acceptance summaries may be committed.
- Do not push to GitHub until the user explicitly asks; local commits are required at each task gate.

---

### Task 1: Deterministic approved-voice finalization

**Files:**
- Create: `backend/src/voxagent/speech/voice_selection.py`
- Create: `backend/tests/speech/test_voice_selection.py`
- Modify: `backend/src/voxagent/speech/tts.py`
- Modify: `backend/src/voxagent/diagnostics/speech_benchmark.py`
- Modify: `backend/tests/speech/test_tts.py`
- Modify: `backend/src/voxagent/cli.py`
- Modify: `backend/tests/test_cli.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class EngineReviewAssignment:
    sample_id: str
    engine: Literal["kokoro", "melo"]
    native_voice_id: int
    text: str

@dataclass(frozen=True, slots=True)
class VoiceReviewAssignment:
    sample_id: str
    native_voice_id: int

class VoiceSelectionError(ValueError):
    pass
```

Implement these exact callable signatures: `build_voice_review_layout(seed: int = VOICE_REVIEW_SEED) -> tuple[tuple[EngineReviewAssignment, ...], tuple[VoiceReviewAssignment, ...]]`, `build_approved_voice_artifacts(review_template: Mapping[str, object]) -> tuple[dict[str, object], dict[str, object]]`, and `publish_approved_voice_artifacts(review_template_path: Path, style_output_path: Path, catalog_output_path: Path) -> None`.

- [ ] **Step 1: Write the failing deterministic-layout tests**

Add tests that call `build_voice_review_layout()` twice and require identical tuples. Assert the complete expected review mapping, including the shuffle-order coupling between the engine pass and voice pass:

```python
assert [(item.sample_id, item.engine, item.native_voice_id, item.text) for item in engines] == [
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
```

Modify the existing `prepare_voice_review` test so its fake synthesizer calls and anonymous filenames prove the function consumes `build_voice_review_layout()` rather than maintaining a second shuffle implementation.

Run:

```powershell
cd backend
& .\.venv\Scripts\python.exe -m pytest tests/speech/test_tts.py tests/speech/test_voice_selection.py -v --basetemp ..\.superpowers\pytest-tmp -p no:cacheprovider
```

Expected RED: import/collection fails because `voice_selection.py` and `build_voice_review_layout` do not exist.

- [ ] **Step 2: Extract the shared seeded review layout**

In `tts.py`, define `TTS_BENCHMARK_TEXTS` once beside the review constants, add the two frozen dataclasses, and implement `build_voice_review_layout` using exactly one `random.Random(seed)` instance. Build engine sources in text-major order with `(kokoro, 3)` followed by `(melo, 0)`, shuffle engine sources first, then shuffle `KOKORO_REVIEW_VOICE_IDS`. Assign anonymous IDs only after each shuffle. Refactor `prepare_voice_review` to map `assignment.engine` to the injected fake/real engine and synthesize `assignment.text` or `VOICE_REVIEW_SENTENCE`.

Import `TTS_BENCHMARK_TEXTS` from `voxagent.speech.tts` in `diagnostics/speech_benchmark.py` and remove its duplicate tuple there. This preserves all existing benchmark text values while making the review generator and unblinder share one source.

Rerun the focused tests. Expected GREEN: deterministic mapping and existing WAV/template tests pass.

- [ ] **Step 3: Write failing approved-artifact tests**

Construct a valid review-template dictionary with schema version `1`, seed `20260830`, six `engine-NNN` entries and eight `voice-NNN` entries. Require `build_approved_voice_artifacts` to return this exact catalog record:

```python
{
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
```

Require the style artifact to contain `schema_version=1`, `selection_method=direct_user_choice`, `review_seed=20260830`, `selected_sample_id=voice-005`, tied engine samples in the approved order, `selected_engine=kokoro`, a non-empty tie-break explanation, resolved native ID `3`, and the exact public profile. Recursively assert that no key named `naturalness`, `intelligibility`, `score`, or `scores` exists.

Parametrize rejections for a wrong seed, missing/duplicate `voice-005`, missing tied engine sample, mismatched anonymous filename, injected numeric score, and a seeded layout that would not resolve the selected voice to ID `3`. Expected RED: import fails because finalization behavior does not exist.

- [ ] **Step 4: Implement strict finalization and CLI publication**

In `voice_selection.py`, keep these approved constants immutable:

```python
APPROVED_VOICE_SAMPLE_ID = "voice-005"
APPROVED_TIED_ENGINE_SAMPLE_IDS = ("engine-002", "engine-003", "engine-004")
APPROVED_ENGINE = "kokoro"
APPROVED_PUBLIC_VOICE = {
    "voice_key": "default_voice",
    "display_name": "声灵默认音色",
    "description": "自然清晰，适合日常对话",
    "gender": "neutral",
    "is_default": True,
    "previewable": True,
}
```

Validate the review template's exact seed, anonymous IDs, filenames, and absence of numeric listener scores before resolving assignments through `build_voice_review_layout`. Require at least one tied engine sample to resolve to Kokoro. Build the style and catalog dictionaries only after every check succeeds. Serialize with `ensure_ascii=False`, `indent=2`, and one trailing newline.

Add this Typer command:

```text
voxagent finalize-voice-review \
  --review-template PATH \
  --style-output PATH \
  --catalog-output PATH
```

The command must not accept a browser/public native ID or an alternate selected sample. Convert `VoiceSelectionError`, `OSError`, and invalid JSON into a clear nonzero Typer error without partially writing either final artifact. Stage both serialized files beside their destinations, validate the staged catalog with `VoiceCatalog.load`, then replace the destinations and remove owned staging files in `finally`.

Add CLI tests for a successful two-file publication and for invalid input leaving both destinations absent. Rerun focused backend tests and `ruff check` for touched files.

- [ ] **Step 5: Commit Task 1**

```powershell
git add backend/src/voxagent/speech/tts.py backend/src/voxagent/speech/voice_selection.py backend/src/voxagent/diagnostics/speech_benchmark.py backend/src/voxagent/cli.py backend/tests/speech/test_tts.py backend/tests/speech/test_voice_selection.py backend/tests/test_cli.py
git commit -m "feat: finalize approved default voice deterministically"
```

Expected: the commit contains source and tests only; production artifacts remain for Task 2.

---

### Task 2: Publish and prove the production voice catalog

**Files:**
- Create: `backend/src/voxagent/speech/voice_catalog.json`
- Create: `benchmarks/tts-voice-style.json`
- Modify: `backend/tests/speech/test_voice_catalog.py`
- Modify: `backend/tests/api/test_voice_socket.py`
- Modify: `docs/superpowers/plans/2026-08-30-02-streaming-voice-loop.md`
- Modify: `.superpowers/sdd/progress.md`

- [ ] **Step 1: Write failing production-catalog and socket privacy tests**

Replace `test_production_catalog_is_blocked_until_blind_review_is_scored` with a test that loads the packaged catalog and asserts:

```python
profile = catalog.get("default_voice")
assert profile.engine == "kokoro"
assert profile.native_voice_id == 3
assert catalog.public_profiles() == (
    PublicVoiceProfile(
        voice_key="default_voice",
        display_name="声灵默认音色",
        description="自然清晰，适合日常对话",
        gender="neutral",
        is_default=True,
        previewable=True,
    ),
)
```

Add one WebSocket test using an orchestrator fixture backed by `load_production_catalog()`. Require `voices.available` to expose exactly the six public fields for `default_voice`; serialize the payload and assert it contains none of `voice-005`, `native_voice_id`, `"engine"`, `kokoro`, `3`, or a drive/path fragment. Expected RED: `load_production_catalog()` still raises because the production JSON is absent.

- [ ] **Step 2: Generate the approved JSON artifacts**

Run the tested command against the already generated local review template:

```powershell
cd backend
& .\.venv\Scripts\voxagent.exe finalize-voice-review `
  --review-template ..\benchmarks\voice-review\review-template.json `
  --style-output ..\benchmarks\tts-voice-style.json `
  --catalog-output .\src\voxagent\speech\voice_catalog.json
```

Inspect both files. The catalog must contain the one exact Task 1 record. The provenance must state direct user choice and the approved tied engine samples, must resolve `voice-005` to Kokoro ID `3`, and must contain no numeric listener-score fields.

- [ ] **Step 3: Verify the selected speaker and packaged startup path**

The committed `benchmarks/kokoro-int8.json` already records successful synthesis for ID `3` on all three fixed benchmark texts. Verify its `engine`, `native_voice_id`, three non-empty text reports, and positive durations in an automated test. Then run one fresh local synthesis smoke using the selected catalog key at each public speed; assert non-empty mono PCM16 WAV and positive native sample rate. Do not replace the measured benchmark with fabricated faster numbers.

Run:

```powershell
cd backend
& .\.venv\Scripts\python.exe -m pytest tests/speech/test_voice_catalog.py tests/speech/test_voice_selection.py tests/api/test_voice_socket.py -v --basetemp ..\.superpowers\pytest-tmp -p no:cacheprovider
& .\.venv\Scripts\voxagent.exe serve --help
```

Expected: production catalog loads; privacy tests pass; the CLI reaches command parsing without the former missing-catalog failure.

- [ ] **Step 4: Reconcile the original Plan 2 ledger and requirements**

Edit the original Plan 2 Task 4 Step 5 to reference the approved direct selection instead of four scored labels. Edit Task 8 to say the server-driven picker renders the single approved voice. Edit Task 9 to exercise `default_voice` at all three speeds rather than a non-default/four-voice catalog. Replace only the old `4.0s` first-token-to-audio ceiling with `15.0s`; retain `natural=1.35s`, incomplete endings near `2.0s`, and `200ms` barge-in.

Change the progress ledger Task 4 line to mark it complete, record the actual first and last seven-character commit hashes for the task, and state `direct voice-005 selection published, review clean`. Never write a guessed hash.

- [ ] **Step 5: Commit Task 2**

```powershell
git add backend/src/voxagent/speech/voice_catalog.json benchmarks/tts-voice-style.json backend/tests/speech/test_voice_catalog.py backend/tests/api/test_voice_socket.py docs/superpowers/plans/2026-08-30-02-streaming-voice-loop.md
git commit -m "feat: publish selected production voice"
```

Expected: `voxagent serve` no longer fails because of a missing voice catalog.

---

### Task 3: Build the accessible Web text-and-voice interface

**Files:**
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/components/ChatMessage.tsx`
- Create: `frontend/src/components/Composer.tsx`
- Create: `frontend/src/components/VoicePicker.tsx`
- Create: `frontend/src/voiceSettings.ts`
- Create: `frontend/src/styles.css`
- Create: `frontend/src/__tests__/App.test.tsx`
- Create: `frontend/src/__tests__/voiceSettings.test.ts`
- Modify: `frontend/src/useVoiceSession.ts`
- Modify: `frontend/src/__tests__/useVoiceSession.test.ts`
- Modify: `frontend/index.html`

**Controller additions:**

```typescript
export type VoiceStatus = "idle" | "listening" | "transcribing" | "thinking" | "speaking";

export interface VoiceSessionController {
  modelId: string | null;
  offline: boolean;
  speakTextReplies: boolean;
  setSpeakTextReplies(enabled: boolean): void;
  // Preserve all existing fields and methods.
}
```

`submitText(text)` reads the current `speakTextReplies` setting and sends it as `text.submit.speak_response`. `vad.stopped` sets `transcribing`; `asr.final` and typed submission set `thinking`; the first playable TTS chunk sets `speaking`; `assistant.done`, cancellation, and resource teardown return to `idle`.

- [ ] **Step 1: Write failing settings tests**

Use only localStorage key `voxagent.voice-settings.v1` and this retained shape:

```typescript
export interface VoiceSettings {
  voiceKey: string | null;
  speed: VoiceSpeed;
  speakTextReplies: boolean;
}
```

Tests must prove malformed JSON falls back to `{voiceKey: null, speed: 1.0, speakTextReplies: false}`, extra/private fields are discarded, invalid speed falls back to `1.0`, and an unknown stored key is replaced by the server-marked default after `voices.available`. Inspect serialized storage and assert it contains no token, transcript, path, audio, engine, native ID, or anonymous sample ID.

Expected RED: `voiceSettings.ts` does not exist.

- [ ] **Step 2: Implement safe settings and extend the session controller**

Implement pure `loadVoiceSettings(storage)`, `saveVoiceSettings(storage, settings)`, and `reconcileVoiceSettings(settings, voices)` functions. Store exactly `voiceKey`, `speed`, and `speakTextReplies`; delete the old `voxagent.voice` key after a successful v1 save.

Move voice persistence out of the hook's current private `storedVoice()` implementation. Expose `modelId`, `offline`, and `speakTextReplies`. Add focused hook tests proving the exact status transitions and proving only later typed turns use the updated auto-speak flag.

Run:

```powershell
cd frontend
& .\node_modules\.bin\vitest.cmd run src/__tests__/voiceSettings.test.ts src/__tests__/useVoiceSession.test.ts
```

- [ ] **Step 3: Write failing conversation and composer tests**

Render `<App controller={fakeController} />`. Assert:

- `Agent（声灵）` messages have `data-side="left"` and user messages named `用户` have `data-side="right"`.
- Voice-origin user messages show `语音输入`; cancelled Agent drafts show `已停止`.
- Completed Agent messages expose `复制` and `朗读`; streaming drafts do not expose `朗读`.
- The header renders `本地运行 · <modelId>` and the current public voice name without a token or path.
- The composer remains enabled while recording, rejects blank text, uses Enter to send, preserves Shift+Enter newline insertion, rejects 4001 Unicode code points, and maps Escape to `cancelActive()`.
- The microphone button toggles `startMicrophone()`/`stopMicrophone()` and an active capture always shows a persistent indicator with text `正在聆听`.
- The auto-speak checkbox calls `setSpeakTextReplies`; disabled microphone permission/error text never disables typed submission.

Expected RED: UI modules do not exist.

- [ ] **Step 4: Write failing single-voice picker tests**

Give the fake controller exactly one `default_voice`. Require one card containing `声灵默认音色`, `自然清晰，适合日常对话`, its selected state, and `试听`. Require only these buttons/options: `慢速 0.8×`, `正常 1.0×`, and `稍快 1.2×`. Selection calls `selectVoice("default_voice", speed)`; preview calls `previewVoice("default_voice", speed)`. Disable preview for `transcribing`, `thinking`, or `speaking`. When `error.code === "preview_failed"`, render `试听失败，可选择其他音色` while leaving the text box and send button usable.

- [ ] **Step 5: Implement the approved interface**

Component responsibilities are fixed:

- `ChatMessage`: semantic article, visible speaker name, left/right data attribute, origin/status labels, copy action, and completed-Assistant speak action.
- `Composer`: controlled one-to-five-line textarea, text validation, Enter/Shift+Enter/Escape behavior, microphone toggle, send, stop, and auto-speak checkbox.
- `VoicePicker`: server-driven cards only, selected public key, three exact speeds, preview button, and busy/error states.
- `App`: connects/disconnects the supplied controller on mount/unmount, maps session state to visible Chinese labels, renders transcript, picker, errors via `aria-live="polite"`, and keeps the composer sticky.
- `main.tsx`: reads the development session token only from `window.location.search`, constructs `ws://127.0.0.1:8765/v1/voice?token=<encoded>`, creates the live controller, and mounts React. It must not accept a non-loopback endpoint and must not write the token to localStorage/sessionStorage or logs.
- `index.html`: add exactly `<script type="module" src="/src/main.tsx"></script>` under the root element.

CSS requirements: neutral light page; centered column `max-width: 960px`; Agent cards `max-width: 72%`; user bubbles `max-width: 68%`; sticky bottom composer; visible focus rings; state communicated by label/icon as well as color; responsive controls remain visible at `1280×720` and `200%` zoom.

Rerun App, settings, hook, and full frontend tests until green.

- [ ] **Step 6: Verify and commit Task 3**

```powershell
cd frontend
& .\node_modules\.bin\vitest.cmd run
& .\node_modules\.bin\tsc.cmd --noEmit
& .\node_modules\.bin\vite.cmd build
cd ..
git add frontend/index.html frontend/src
git commit -m "feat: add accessible Web voice chat"
```

Expected: tests and type checking exit zero and `frontend/dist` contains an executable client bundle rather than an empty shell.

---

### Task 4: End-to-end acceptance, runbook, and local trial

**Files:**
- Create: `backend/tests/e2e/test_voice_loop.py`
- Create: `benchmarks/voice-loop-acceptance.json`
- Modify: `README.md`
- Modify: `docs/plan-01/README.md`
- Modify: `.superpowers/sdd/progress.md`

- [ ] **Step 1: Write the failing synthetic WebSocket E2E test**

Use a real FastAPI `TestClient` WebSocket and fakes only at ASR/LLM/TTS boundaries. Use the real `ConversationOrchestrator`, endpoint detector, catalog loader, protocol parser, and socket writer. In one socket session:

1. Receive `session.ready` and the one-entry public catalog.
2. Submit a typed turn with spoken response enabled.
3. Feed one complete PCM16 voice turn and receive its final transcript.
4. Prove both turns reach one ordered in-memory history.
5. Start an old spoken reply, interrupt it with new text, and prove no later old-turn audio is delivered.
6. Select/preview `default_voice` at `0.8`, `1.0`, and `1.2`; prove each public key/speed reaches the fake TTS.
7. Serialize every client/server JSON frame and assert that `voice-005`, `native_voice_id`, Kokoro, native ID `3`, filesystem paths, and model directories never cross the socket.

Expected RED: the E2E file does not exist and any uncovered integration mismatch fails explicitly.

- [ ] **Step 2: Implement only the fixtures/adapters required to make E2E green**

Do not add a second protocol or duplicate orchestration. Keep deterministic fake audio small but valid PCM16 WAV. Run the E2E file, then the complete backend suite and Ruff.

- [ ] **Step 3: Add an exact local runbook**

Document these terminals from the worktree root, with one freshly generated 32-byte URL-safe token reused only for that run:

```powershell
# Terminal 1 — Ollama (skip Start-Process when port 11434 is already listening)
$env:OLLAMA_MODELS = 'D:\VoxAgentData\models\ollama'
$env:OLLAMA_NO_CLOUD = '1'
$env:OLLAMA_HOST = '127.0.0.1:11434'
Start-Process -FilePath 'D:\VoxAgentData\runtime\ollama\ollama.exe' -ArgumentList 'serve' -WindowStyle Hidden

# Generate once, then paste the printed value into $token in both terminals.
$token = [Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).TrimEnd('=').Replace('+','-').Replace('/','_')
$token

# Terminal 2 — backend
$env:VOXAGENT_DATA_ROOT = 'D:\VoxAgentData'
cd 'D:\Agent_protect\VoxAgent（声灵）\.worktrees\phase-02-voice-loop\backend'
& .\.venv\Scripts\voxagent.exe serve --session-token $token --port 8765

# Terminal 3 — Web client
cd 'D:\Agent_protect\VoxAgent（声灵）\.worktrees\phase-02-voice-loop\frontend'
& .\node_modules\.bin\vite.cmd --host 127.0.0.1 --port 5173
# Open http://127.0.0.1:5173/?token=<the URL-safe token printed above>
```

State explicitly that `/healthz` contains no token and the token is never committed or placed in localStorage. Add stop instructions that target only the two foreground development terminals; never stop GPT, Codex, Roxi, NVIDIA services, or unrelated processes.

- [ ] **Step 4: Run complete automated verification**

```powershell
cd backend
& .\.venv\Scripts\python.exe -m pytest -v --basetemp ..\.superpowers\pytest-tmp -p no:cacheprovider
& .\.venv\Scripts\ruff.exe check src tests
cd ..\frontend
& .\node_modules\.bin\vitest.cmd run
& .\node_modules\.bin\tsc.cmd --noEmit
& .\node_modules\.bin\vite.cmd build
git diff --check
git status --short
```

Expected: every command exits zero; only intentional Plan 2 files are modified.

- [ ] **Step 5: Perform target-machine acceptance and write measured evidence**

Start the real loopback backend and Vite client with the runbook. Record, without fabrication:

- five typed turns;
- five normal voice endings;
- five incomplete/filler endings;
- five barge-ins split between text and speech;
- one preview and one spoken response at each speed;
- one refresh proving only `{voiceKey:"default_voice", speed, speakTextReplies}` persists;
- one microphone-permission denial followed by a successful typed turn;
- visible `Agent（声灵）` left and `用户` right;
- browser inspection finding neither `voice-005` nor native ID/private model data.

Write `benchmarks/voice-loop-acceptance.json` with `schema_version=1`, UTC capture time, exact model/public voice IDs, counts, p50/p95 values for `endpoint_to_transcript_seconds`, `transcript_to_first_token_seconds`, `first_token_to_audio_seconds`, `barge_in_stop_ms`, pass/fail booleans for every gate, and a note that the Kokoro latency ceiling was user-approved at `15.0s`. Never mark an unperformed check as passed; use `null` plus `not_run` if hardware/browser access blocks it.

Acceptance requires: natural mode does not finish at `0.9s`; incomplete endings wait approximately `2.0s`; p95 first-token-to-audio is at most `15.0s`; every barge-in stops playback within `200ms` without stale audio; text works after microphone denial; all three speeds preview/respond; and no private voice identifier appears in the browser.

- [ ] **Step 6: Commit Task 4**

```powershell
git add backend/tests/e2e/test_voice_loop.py benchmarks/voice-loop-acceptance.json README.md docs/plan-01/README.md
git commit -m "test: validate local Web voice loop"
```

After task review is clean, update the ignored progress ledger with real commit hashes for Tasks 4, 8, and 9. Do not add `.superpowers/sdd/progress.md` to Git.

---

## Final Review and Branch Completion

- [ ] Generate a whole-branch review package from the branch merge-base through `HEAD` and dispatch the final reviewer using the requesting-code-review template.
- [ ] Send all Critical/Important findings together to one fix agent, rerun covering tests, and re-review until both spec compliance and code quality are approved.
- [ ] Run the complete Task 4 verification commands again after the last fix.
- [ ] Use the finishing-a-development-branch skill to present merge/push/keep-worktree choices. Do not merge or push without the user's explicit choice.
- [ ] If the user wants an immediate trial, start only Ollama, the Plan 2 backend, and Vite on loopback, then give the exact local URL. Keep GPT/Codex and Roxi running.
