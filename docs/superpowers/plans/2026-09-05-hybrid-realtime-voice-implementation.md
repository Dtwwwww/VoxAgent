# Hybrid Realtime Voice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a free, browser-first realtime Mandarin voice mode with explicit Realtek microphone selection, live captions, sentence-level speech, interruption, and automatic local fallback.

**Architecture:** Add isolated device, Web Speech, and realtime orchestration units on the frontend; keep `useVoiceSession` as the WebSocket/message owner and wire it to the realtime engine through narrow callbacks. Extend the frozen WebSocket protocol only for browser-finalized voice transcripts and local partial transcripts; reuse the existing Ollama, RAG, memory, cancellation, and conversation persistence path.

**Tech Stack:** React 19, TypeScript 7, Web Audio API, MediaDevices, Web Speech API, WebSocket, Vitest/Testing Library, FastAPI, Pydantic, pytest, sherpa-onnx SenseVoice/Paraformer/Kokoro, Ollama.

## Global Constraints

- Ollama and all LLM inference remain local at `127.0.0.1:11434`.
- Default mode is `online-preferred`; the user can choose `local-only`.
- The online path uses browser-provided speech services and requires no paid API key.
- Edge TTS remains an optional future experiment, and CosyVoice/GPU scheduling remains out of scope for this first version.
- The target input is the built-in Realtek microphone; virtual ToDesk/XReal inputs must be visibly labelled.
- Typed messages continue to send `speak_response: false` and never trigger automatic speech.
- Browser speech failure must fall back locally without closing the WebSocket or clearing conversation/RAG/memory state.
- Raw microphone audio is never persisted; only the selected device ID/name and mode may be stored in browser storage.
- Remove the current user-visible Melo voice because target-machine roundtrip recognition proved it unintelligible.
- Online p95 targets: level feedback ≤100 ms, first interim caption 0.5–1.5 s, interruption ≤300 ms, first speech 0.3–1 s after the first complete sentence.
- Preserve all pre-existing worktree changes and never add `.token_tmp` to Git.
- Run commands from `D:\Agent_protect\VoxAgent（声灵）\.worktrees\phase-02-voice-loop` unless a step says otherwise.
- Frontend command prefix on the target machine:

```powershell
$voxNode = 'C:\Users\DTW001128\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
```

---

## File Map

### New frontend units

- `frontend/src/audio/devices.ts`: enumerate, classify, and select microphone devices.
- `frontend/src/audio/devices.test.ts`: unit tests for Realtek preference, virtual labels, and persistence.
- `frontend/src/audio/web-speech.d.ts`: minimal browser speech declarations missing from TypeScript DOM types.
- `frontend/src/audio/webSpeech.ts`: Web Speech recognition/synthesis adapter and capability detection.
- `frontend/src/audio/webSpeech.test.ts`: adapter tests with deterministic recognition and synthesis fakes.
- `frontend/src/realtime/sentenceQueue.ts`: stable incremental sentence extraction for streaming assistant text.
- `frontend/src/realtime/sentenceQueue.test.ts`: sentence boundary and cancellation tests.
- `frontend/src/realtime/RealtimeVoiceEngine.ts`: provider selection, state transitions, interruption, and fallback.
- `frontend/src/realtime/RealtimeVoiceEngine.test.ts`: state-machine and fallback tests.
- `frontend/src/components/RealtimeVoiceControls.tsx`: call toggle, mode/device selectors, meter, and provider label.
- `frontend/src/components/RealtimeVoiceControls.test.tsx`: accessible UI behavior tests.

### Existing frontend integration points

- `frontend/src/audio/capture.ts`: selected-device constraints, level callback, track exposure, optional PCM forwarding.
- `frontend/src/useVoiceSession.ts`: connect engine events to WebSocket messages and conversation state.
- `frontend/src/protocol.ts`: strict `voice.transcript.submit` and `asr.partial` wire types.
- `frontend/src/voiceSettings.ts`: persist speech mode, selected microphone, and browser voice.
- `frontend/src/App.tsx`: place realtime controls above the composer and keep the bottom status bar.
- `frontend/src/components/Composer.tsx`: replace push-to-talk semantics with realtime call semantics while preserving text input.
- `frontend/src/components/VoicePicker.tsx`: combine browser Chinese voices with the single local fallback voice.
- `frontend/src/components/VoiceStatus.tsx`, `frontend/src/presentation.ts`, `frontend/src/styles.css`: approved states and responsive presentation.
- `frontend/src/__tests__/useVoiceSession.test.ts`, `frontend/src/__tests__/App.test.tsx`, `frontend/src/__tests__/voiceSettings.test.ts`: integration regressions.

### Backend and contract integration points

- `backend/src/voxagent/conversation/events.py`: `VoiceTranscriptSubmit` and `AsrPartial` models.
- `backend/src/voxagent/conversation/orchestrator.py`: browser-transcript entry point and emitted local partial results.
- `backend/src/voxagent/api/app.py`: dispatch the new client event.
- `backend/src/voxagent/speech/voice_catalog.json`, `backend/src/voxagent/cli.py`: expose/load only intelligible Kokoro local fallback.
- `contracts/protocol-fixtures.json`: valid/invalid cross-language protocol fixtures.
- `backend/tests/api/test_protocol.py`, `backend/tests/api/test_voice_socket.py`, `backend/tests/conversation/test_orchestrator.py`, `backend/tests/e2e/test_voice_loop.py`: backend contract and flow tests.
- `backend/tests/speech/test_voice_catalog.py`: local catalog quality gate.
- `benchmarks/tts-roundtrip-20260905.json`, `benchmarks/tts-voice-style.json`: preserve target-machine evidence and selected fallback.
- `docs/plan-02/README.md`: startup, privacy, device selection, modes, and manual acceptance instructions.

---

### Task 1: Establish an intelligible local fallback baseline

**Files:**
- Modify: `backend/src/voxagent/speech/voice_catalog.json`
- Modify: `backend/src/voxagent/cli.py`
- Modify: `backend/tests/speech/test_voice_catalog.py`
- Create: `benchmarks/tts-roundtrip-20260905.json`
- Modify: `benchmarks/tts-voice-style.json`
- Include in commit: the already-implemented TTS lifecycle/copy/replay fixes listed by `git status`, except `.token_tmp`

**Interfaces:**
- Produces: one public local voice with `voice_key="default_voice"`, `engine="kokoro"`, `native_voice_id=3`.
- Produces: production `_CatalogTts` containing only an initialized `kokoro` engine.
- Consumes: the existing `VoiceCatalog`, `SherpaOfflineTts`, and approved replay request-id changes.

- [ ] **Step 1: Write the failing catalog and production-factory tests**

Add assertions to `backend/tests/speech/test_voice_catalog.py`:

```python
def test_production_catalog_exposes_only_intelligible_local_fallback():
    catalog = load_production_catalog()
    profiles = catalog.public_profiles()
    assert [(item.voice_key, item.engine, item.native_voice_id) for item in profiles] == [
        ("default_voice", "kokoro", 3)
    ]
    assert profiles[0].is_default is True
```

Add a focused factory assertion to the existing `backend/tests/test_cli.py` that patches `SherpaOfflineTts.from_model_dir`, calls `_create_production_app`, and verifies no call has `engine="melo"`.

- [ ] **Step 2: Run the tests and verify the current Melo catalog fails**

```powershell
& '.\backend\.venv\Scripts\python.exe' -m pytest backend/tests/speech/test_voice_catalog.py backend/tests/test_cli.py -q -p no:cacheprovider --basetemp="$env:TEMP\voxagent-pytest-task-1-red"
```

Expected: FAIL because `default_voice` currently resolves to Melo and production initializes both engines.

- [ ] **Step 3: Replace the local public catalog and record measured evidence**

Set `backend/src/voxagent/speech/voice_catalog.json` to:

```json
{
  "voices": [
    {
      "voice_key": "default_voice",
      "display_name": "声灵离线音色",
      "description": "中文清楚；完全离线时生成较慢",
      "gender": "neutral",
      "engine": "kokoro",
      "native_voice_id": 3,
      "is_default": true,
      "previewable": true
    }
  ]
}
```

In `_create_production_app`, construct `_CatalogTts` with only:

```python
{
    "kokoro": SherpaOfflineTts.from_model_dir(
        _speech_model_directory(root, "kokoro-int8-zh-en"),
        0,
        engine="kokoro",
        catalog=catalog,
    )
}
```

Create `benchmarks/tts-roundtrip-20260905.json` with the measured sentence, target-machine identity, synthesis time, duration, RMS, and roundtrip transcript for both engines. Update `tts-voice-style.json` so the active local fallback is Kokoro voice 3 and the reason is the measured intelligibility gate, not preference alone.

- [ ] **Step 4: Run focused and existing voice tests**

```powershell
& '.\backend\.venv\Scripts\python.exe' -m pytest backend/tests/speech/test_voice_catalog.py backend/tests/conversation/test_orchestrator.py -q -p no:cacheprovider --basetemp="$env:TEMP\voxagent-pytest-task-1-green"
```

Expected: PASS; no test expects a user-visible Melo voice.

- [ ] **Step 5: Verify and commit the current Plan 2 baseline without secrets**

```powershell
git diff --check
git status --short
git add backend/src/voxagent/api/app.py backend/src/voxagent/cli.py backend/src/voxagent/conversation/events.py backend/src/voxagent/conversation/orchestrator.py backend/src/voxagent/speech/voice_catalog.json backend/src/voxagent/speech/text_normalization.py backend/tests/api/test_protocol.py backend/tests/api/test_voice_socket.py backend/tests/conversation/test_orchestrator.py backend/tests/e2e/test_voice_loop.py backend/tests/speech/test_voice_catalog.py backend/tests/speech/test_text_normalization.py backend/tests/test_cli.py benchmarks/tts-roundtrip-20260905.json benchmarks/tts-voice-style.json contracts/protocol-fixtures.json docs/plan-02/README.md docs/superpowers/plans/2026-09-05-hybrid-realtime-voice-implementation.md docs/superpowers/plans/2026-09-05-tts-playback-copy-feedback-plan.md frontend/src/__tests__/useVoiceSession.test.ts frontend/src/audio/playback.ts frontend/src/components/ChatMessage.test.tsx frontend/src/components/ChatMessage.tsx frontend/src/presentation.ts frontend/src/protocol.ts frontend/src/useVoiceSession.ts
git diff --cached --check
git commit -m "fix: stabilize intelligible local speech playback"
```

Expected: the commit includes the previously verified replay/copy fixes plus the Kokoro fallback; `.token_tmp` remains untracked.

---

### Task 2: Add deterministic microphone selection and level diagnostics

**Files:**
- Create: `frontend/src/audio/devices.ts`
- Create: `frontend/src/audio/devices.test.ts`
- Modify: `frontend/src/audio/capture.ts`
- Modify: `frontend/src/__tests__/useVoiceSession.test.ts`

**Interfaces:**
- Produces: `MicrophoneDevice`, `listMicrophones()`, `choosePreferredMicrophone()`, `browserDefaultMatchesSelection()`, `microphoneConstraints()`.
- Produces: `MicrophoneCapture.start(options): Promise<MediaStreamTrack>` and level callbacks.
- Consumes: `StorageLike` from `frontend/src/voiceSettings.ts` for persistence in Task 6.

- [ ] **Step 1: Write failing device-selection tests**

Create `frontend/src/audio/devices.test.ts` covering these exact cases:

```ts
expect(classifyMicrophone({ deviceId: "r", kind: "audioinput", label: "麦克风阵列 (Realtek(R) Audio", groupId: "g" } as MediaDeviceInfo).virtual).toBe(false);
expect(classifyMicrophone({ deviceId: "v", kind: "audioinput", label: "ToDesk Virtual Audio", groupId: "g" } as MediaDeviceInfo).virtual).toBe(true);
expect(choosePreferredMicrophone(devices, null)?.deviceId).toBe("r");
expect(choosePreferredMicrophone(devices, "v")?.deviceId).toBe("v");
expect(browserDefaultMatchesSelection(devices, "r")).toBe(true);
expect(microphoneConstraints("r")).toEqual({
  audio: {
    deviceId: { exact: "r" },
    channelCount: 1,
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  },
});
```

- [ ] **Step 2: Run the new test and verify missing exports fail**

```powershell
& $voxNode '.\frontend\node_modules\vitest\vitest.mjs' run --root frontend src/audio/devices.test.ts
```

Expected: FAIL because `devices.ts` does not exist.

- [ ] **Step 3: Implement the device unit**

Use these public types and signatures:

```ts
export interface MicrophoneDevice {
  deviceId: string;
  groupId: string;
  label: string;
  virtual: boolean;
  preferred: boolean;
  browserDefault: boolean;
}

export async function listMicrophones(): Promise<MicrophoneDevice[]>;
export function classifyMicrophone(device: MediaDeviceInfo): MicrophoneDevice;
export function choosePreferredMicrophone(
  devices: readonly MicrophoneDevice[],
  savedDeviceId: string | null,
): MicrophoneDevice | null;
export function browserDefaultMatchesSelection(
  devices: readonly MicrophoneDevice[],
  selectedDeviceId: string | null,
): boolean;
export function microphoneConstraints(deviceId: string | null): MediaStreamConstraints;
```

Virtual-name matching is case-insensitive for `virtual`, `todesk`, `xreal`, `vb-audio`, and `stereo mix`. Preferred-name matching covers `realtek`, `microphone array`, and `麦克风阵列`. `browserDefaultMatchesSelection` returns true only when the selected entry is itself the `default` entry or its non-empty `groupId` equals the browser default entry's `groupId`; unknown/empty IDs return false.

- [ ] **Step 4: Extend capture with selected track, meter, and optional PCM transport**

Use this options contract in `capture.ts`:

```ts
export interface MicrophoneCaptureOptions {
  deviceId: string | null;
  forwardPcm: boolean;
  onFrame(frame: ArrayBuffer): void;
  onLevel(level: number): void;
  onSettings(settings: MediaTrackSettings): void;
}

export class MicrophoneCapture {
  constructor(private readonly options: MicrophoneCaptureOptions) {}
  async start(signal?: AbortSignal): Promise<MediaStreamTrack>;
  setForwardPcm(enabled: boolean): void;
  async stop(): Promise<void>;
}
```

Store `forwardPcm` as mutable capture state initialized from `options.forwardPcm`; `setForwardPcm` updates only that flag and never reacquires the device. Build constraints from `navigator.mediaDevices.getSupportedConstraints()` so unsupported enhancement keys are omitted, while an explicitly selected device keeps its exact `deviceId`. Compute RMS from each worklet frame, map it to `[0, 1]`, call `onLevel`, and only packetize/send PCM when the mutable flag is true. Return the live `stream.getAudioTracks()[0]`, call `onSettings(track.getSettings())` for diagnostics, and throw a recoverable setup error when no live audio track exists.

- [ ] **Step 5: Verify device and existing PCM behavior**

```powershell
& $voxNode '.\frontend\node_modules\vitest\vitest.mjs' run --root frontend src/audio/devices.test.ts src/__tests__/useVoiceSession.test.ts -t "microphone|PCM"
```

Expected: PASS, including exact 640-byte PCM frames in local mode and no binary frames when `forwardPcm=false`.

- [ ] **Step 6: Commit**

```powershell
git add frontend/src/audio/devices.ts frontend/src/audio/devices.test.ts frontend/src/audio/capture.ts frontend/src/__tests__/useVoiceSession.test.ts
git commit -m "feat: add selectable microphone diagnostics"
```

---

### Task 3: Build the browser recognition and speech adapters

**Files:**
- Create: `frontend/src/audio/web-speech.d.ts`
- Create: `frontend/src/audio/webSpeech.ts`
- Create: `frontend/src/audio/webSpeech.test.ts`
- Create: `frontend/src/realtime/sentenceQueue.ts`
- Create: `frontend/src/realtime/sentenceQueue.test.ts`

**Interfaces:**
- Produces: `BrowserSpeechProvider`, `BrowserVoice`, `BrowserSpeechCallbacks`, `BrowserSpeechFailure`.
- Produces: `StreamingSentenceQueue.push(delta)` and `.cancel()`.
- Consumes: a live `MediaStreamTrack` selected by Task 2.

- [ ] **Step 1: Write failing Web Speech capability tests**

Cover:

```ts
expect(BrowserSpeechProvider.isSupported(fakeWindow)).toBe(true);
await provider.start(selectedTrack, callbacks, false);
expect(recognition.start).toHaveBeenCalledWith(selectedTrack);
recognition.emitInterim("今天天气");
expect(callbacks.onInterim).toHaveBeenCalledWith("今天天气");
recognition.emitFinal("今天天气怎么样");
expect(callbacks.onFinal).toHaveBeenCalledWith("今天天气怎么样");
```

Also test both branches after a thrown `TypeError` from `start(track)`: `allowDefaultInputFallback=false` yields `{ code: "track_not_supported", recoverable: true }` without a parameterless call, while `true` makes exactly one parameterless `start()` call.

- [ ] **Step 2: Write failing synthesis and sentence-queue tests**

Require Chinese filtering and ordered sentences:

```ts
expect(provider.voices().map((voice) => voice.lang)).toEqual(["zh-CN", "zh-TW"]);
expect(queue.push("你好，我是声灵。今天")).toEqual(["你好，我是声灵。"]);
expect(queue.push("想和你聊天！")).toEqual(["今天想和你聊天！"]);
queue.cancel();
expect(queue.push("旧回答不应继续。" )).toEqual([]);
```

- [ ] **Step 3: Run tests and verify they fail before implementation**

```powershell
& $voxNode '.\frontend\node_modules\vitest\vitest.mjs' run --root frontend src/audio/webSpeech.test.ts src/realtime/sentenceQueue.test.ts
```

Expected: FAIL because the modules do not exist.

- [ ] **Step 4: Implement strict browser adapters**

Use these interfaces:

```ts
export interface BrowserSpeechCallbacks {
  onInterim(text: string): void;
  onFinal(text: string): void;
  onSpeechStart(): void;
  onSpeechEnd(): void;
  onRecognitionEnd(): void;
  onError(error: BrowserSpeechFailure): void;
}

export interface BrowserVoice {
  key: string;
  name: string;
  lang: string;
  localService: boolean;
}

export class BrowserSpeechProvider {
  static isSupported(scope?: Window): boolean;
  start(
    track: MediaStreamTrack,
    callbacks: BrowserSpeechCallbacks,
    allowDefaultInputFallback: boolean,
  ): Promise<void>;
  stopRecognition(): void;
  voices(): BrowserVoice[];
  speak(text: string, voiceKey: string | null, rate: number): Promise<void>;
  cancelSpeech(): void;
  close(): void;
}
```

Recognition uses `lang="zh-CN"`, `continuous=true`, `interimResults=true`, and `maxAlternatives=1`. It first calls `recognition.start(track)`. If that throws `TypeError`, it may call parameterless `start()` exactly once only when `allowDefaultInputFallback` is true; otherwise it reports `track_not_supported`. Wire the recognition object's natural `onend` separately from speech-segment `onspeechend`. Synthesis resolves on `utterance.onend`, rejects on `utterance.onerror`, and selects only voices whose language starts with `zh` or whose name contains `Chinese`, `中文`, `Xiaoxiao`, `Yunxi`, `Huihui`, or `Yaoyao`.

`StreamingSentenceQueue` emits at `。！？!?；;\n`, caps a sentence at 80 Unicode code points, preserves the remainder, and ignores pushes after cancellation until `reset()`.

- [ ] **Step 5: Run adapter tests and typecheck**

```powershell
& $voxNode '.\frontend\node_modules\vitest\vitest.mjs' run --root frontend src/audio/webSpeech.test.ts src/realtime/sentenceQueue.test.ts
& $voxNode '.\frontend\node_modules\typescript\bin\tsc' --noEmit -p frontend/tsconfig.json
```

Expected: PASS with no ambient `any` leakage outside `web-speech.d.ts`.

- [ ] **Step 6: Commit**

```powershell
git add frontend/src/audio/web-speech.d.ts frontend/src/audio/webSpeech.ts frontend/src/audio/webSpeech.test.ts frontend/src/realtime/sentenceQueue.ts frontend/src/realtime/sentenceQueue.test.ts
git commit -m "feat: add browser realtime speech adapters"
```

---

### Task 4: Extend the frozen protocol for browser and local transcripts

**Files:**
- Modify: `backend/src/voxagent/conversation/events.py`
- Modify: `backend/src/voxagent/conversation/orchestrator.py`
- Modify: `backend/src/voxagent/api/app.py`
- Modify: `frontend/src/protocol.ts`
- Modify: `contracts/protocol-fixtures.json`
- Modify: `backend/tests/api/test_protocol.py`
- Modify: `backend/tests/api/test_voice_socket.py`
- Modify: `backend/tests/conversation/test_orchestrator.py`
- Modify: `frontend/src/__tests__/useVoiceSession.test.ts`

**Interfaces:**
- Produces client event: `{ type: "voice.transcript.submit", text: string }`.
- Produces server event: `{ type: "asr.partial", session_id: UUID, turn_id: int, text: string }`.
- Produces: `ConversationOrchestrator.submit_voice_transcript(text: str) -> AsyncIterator[Output]`.
- Consumes: existing `AsrFinal`, `_start_reply`, cancellation, persistence, context assembly, and memory proposal paths.

- [ ] **Step 1: Add invalid/valid fixtures and failing parser tests**

Add to `valid_client`:

```json
{"type":"voice.transcript.submit","text":"你好，声灵"}
```

Add invalid empty/oversized/unexpected-field cases. Add to `valid_server`:

```json
{"type":"asr.partial","session_id":"00000000-0000-4000-8000-000000000001","turn_id":1,"text":"你好"}
```

Add invalid empty text and non-strict `turn_id` fixtures. Both Python and TypeScript fixture tests must initially fail.

- [ ] **Step 2: Add failing orchestrator behavior tests**

Require browser transcript submission to emit the same voice identity without local TTS:

```python
outputs = [item async for item in orchestrator.submit_voice_transcript("浏览器识别结果")]
assert [item.type for item in outputs] == ["asr.final", "assistant.delta", "assistant.done"]
assert outputs[0].text == "浏览器识别结果"
assert orchestrator.history.messages_for_model()[-2:] == (
    ChatMessage("user", "浏览器识别结果"),
    ChatMessage("assistant", "测试回答"),
)
assert store.calls[0] == ("user", 1, "浏览器识别结果", "voice")
assert tts.calls == []
```

Require updated partial ASR to yield exactly one `asr.partial` event and unchanged partial results to yield none.

- [ ] **Step 3: Run protocol/orchestrator tests and verify failure**

```powershell
& '.\backend\.venv\Scripts\python.exe' -m pytest backend/tests/api/test_protocol.py backend/tests/conversation/test_orchestrator.py -k "transcript or partial or protocol" -q -p no:cacheprovider --basetemp="$env:TEMP\voxagent-pytest-task-4-red"
& $voxNode '.\frontend\node_modules\vitest\vitest.mjs' run --root frontend src/__tests__/useVoiceSession.test.ts -t "protocol"
```

Expected: FAIL on unknown event types/missing methods.

- [ ] **Step 4: Implement strict event models and parsers**

Backend models:

```python
class VoiceTranscriptSubmit(ClientMessage):
    type: Literal["voice.transcript.submit"]
    text: str

    @field_validator("text")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not 1 <= len(normalized) <= 4000:
            raise ValueError("text must contain between 1 and 4000 Unicode code points")
        return normalized

class AsrPartial(TurnServerMessage):
    type: Literal["asr.partial"]
    text: str

    @field_validator("text")
    @classmethod
    def require_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("partial transcript must be non-empty")
        return value
```

Add both to the discriminated unions. Mirror exact-key parsing in `frontend/src/protocol.ts`; `voice.transcript.submit` must use the same Unicode 1–4000 rule as `text.submit`.

- [ ] **Step 5: Implement browser transcript submission through the normal reply pipeline**

Refactor `submit_text` into one private `_submit_text_turn(text, *, origin, speak_response, echo_asr)` async generator. Move the current normalization, lock, cancellation, persistence, `history.add_user`, `_start_reply`, drain, and `_finish_active` behavior into that helper; replace the hardcoded source `"text"` with `origin`. Keep these two public wrappers exactly:

```python
async def submit_text(self, text: str, speak_response: bool) -> AsyncIterator[Output]:
    async for item in self._submit_text_turn(
        text, origin="text", speak_response=speak_response, echo_asr=False
    ):
        yield item

async def submit_voice_transcript(self, text: str) -> AsyncIterator[Output]:
    async for item in self._submit_text_turn(
        text, origin="voice", speak_response=False, echo_asr=True
    ):
        yield item
```

When `echo_asr` is true, emit `AsrFinal` before assistant deltas. Keep the same cancellation, persistence, context, memory proposal, and history semantics.

In `_dispatch_event`, route `VoiceTranscriptSubmit` to `submit_voice_transcript(event.text)`.

- [ ] **Step 6: Emit local partial events without duplicate text**

Change `_finish_partial` to return `AsrPartial | Literal[False] | ErrorMessage`. Return `False` for a cancelled/stale/unchanged result, return the existing recoverable `ErrorMessage` on recognizer failure, and return an `AsrPartial` populated from the current token only when trimmed text is non-empty and differs from `self._partial_text`. Update each `accept_audio` call site to yield an `AsrPartial` or `ErrorMessage` result and otherwise continue. Do not persist partial text and do not add it to conversation history.

- [ ] **Step 7: Run contract and API regressions**

```powershell
& '.\backend\.venv\Scripts\python.exe' -m pytest backend/tests/api/test_protocol.py backend/tests/api/test_voice_socket.py backend/tests/conversation/test_orchestrator.py backend/tests/e2e/test_voice_loop.py -q -p no:cacheprovider --basetemp="$env:TEMP\voxagent-pytest-task-4-green"
& $voxNode '.\frontend\node_modules\vitest\vitest.mjs' run --root frontend src/__tests__/useVoiceSession.test.ts
```

Expected: PASS; typed text remains silent, browser voice submissions carry `origin="voice"`, partial text is never duplicated as a final message.

- [ ] **Step 8: Commit**

```powershell
git add backend/src/voxagent/conversation/events.py backend/src/voxagent/conversation/orchestrator.py backend/src/voxagent/api/app.py frontend/src/protocol.ts contracts/protocol-fixtures.json backend/tests/api/test_protocol.py backend/tests/api/test_voice_socket.py backend/tests/conversation/test_orchestrator.py backend/tests/e2e/test_voice_loop.py frontend/src/__tests__/useVoiceSession.test.ts
git commit -m "feat: add realtime voice transcript protocol"
```

---

### Task 5: Implement the isolated realtime voice state machine

**Files:**
- Create: `frontend/src/realtime/RealtimeVoiceEngine.ts`
- Create: `frontend/src/realtime/RealtimeVoiceEngine.test.ts`

**Interfaces:**
- Consumes: `MicrophoneCapture`, `BrowserSpeechProvider`, `StreamingSentenceQueue`.
- Consumes callbacks: JSON send, snapshot updates, and terminal error updates. Binary PCM sending remains the capture's `onFrame` callback owned by `useVoiceSession`.
- Produces: `RealtimeVoiceEngine.start`, `.stop`, and `.handleServerEvent`.

- [ ] **Step 1: Write failing state-transition tests**

Use fakes and verify the exact happy path:

```ts
await engine.start({
  mode: "online-preferred",
  deviceId: "realtek",
  browserVoiceKey: "zh-voice",
  speechRate: 1,
  allowDefaultInputFallback: false,
});
expect(states).toContainEqual({ state: "listening", provider: "browser" });
recognizer.onInterim("你好");
expect(interims.at(-1)).toBe("你好");
recognizer.onFinal("你好声灵");
expect(sentJson.at(-1)).toEqual({ type: "voice.transcript.submit", text: "你好声灵" });
engine.handleServerEvent({ type: "asr.final", session_id: SESSION_ID, turn_id: 4, text: "你好声灵" });
engine.handleServerEvent({
  type: "assistant.delta",
  session_id: SESSION_ID,
  turn_id: 4,
  delta: "你好！今天想聊什么？",
});
expect(spoken).toEqual(["你好！", "今天想聊什么？"]);
```

- [ ] **Step 2: Write failing interruption and fallback tests**

Require:

```ts
recognizer.onSpeechStart();
expect(browserSpeech.cancelSpeech).toHaveBeenCalledOnce();
expect(sentJson).toContainEqual({ type: "turn.cancel" });
expect(states.at(-1)?.state).toBe("user_speaking");

recognizer.onError({ code: "network", recoverable: true });
expect(states).toContainEqual({ state: "listening", provider: "local", fallbackReason: "network" });
expect(capture.setForwardPcm).toHaveBeenCalledWith(true);
```

Also test `local-only` never constructs/starts browser recognition, and stop cancels recognition, speech, capture, sentence queue, and pending turn exactly once.
Use fake timers to prove a natural recognition end restarts after 150 ms while active, a final transcript resets the empty-run counter, and two consecutive runs without a final result switch only once to local.

- [ ] **Step 3: Run tests and verify missing engine failure**

```powershell
& $voxNode '.\frontend\node_modules\vitest\vitest.mjs' run --root frontend src/realtime/RealtimeVoiceEngine.test.ts
```

Expected: FAIL because the engine does not exist.

- [ ] **Step 4: Implement the explicit state contract**

Use:

```ts
export type SpeechMode = "online-preferred" | "local-only";
export type ActiveSpeechProvider = "browser" | "local";
export type RealtimeVoiceState =
  | "off" | "connecting" | "listening" | "user_speaking"
  | "transcribing" | "thinking" | "responding" | "speaking" | "fallback";
export type RealtimeNotice = "interrupted" | null;

export interface RealtimeSnapshot {
  active: boolean;
  state: RealtimeVoiceState;
  provider: ActiveSpeechProvider | null;
  interimText: string;
  inputLevel: number;
  fallbackReason: string | null;
  notice: RealtimeNotice;
}

export interface RealtimeVoiceDependencies {
  capture: MicrophoneCapture;
  browserSpeech: BrowserSpeechProvider;
  sentenceQueue: StreamingSentenceQueue;
  sendJson(event: ClientEvent): void;
  onSnapshot(snapshot: RealtimeSnapshot): void;
  onTerminalError(error: BrowserSpeechFailure): void;
}

export interface StartRealtimeOptions {
  mode: SpeechMode;
  deviceId: string | null;
  browserVoiceKey: string | null;
  speechRate: number;
  allowDefaultInputFallback: boolean;
}

export class RealtimeVoiceEngine {
  constructor(dependencies: RealtimeVoiceDependencies);
  start(options: StartRealtimeOptions): Promise<void>;
  stop(): Promise<void>;
  handleServerEvent(event: ServerEvent): void;
}
```

The engine must use a monotonically increasing generation number. Every async completion checks its captured generation before publishing state or audio. This prevents stopped calls, old recognition callbacks, and old speech promises from reactivating a new call.

- [ ] **Step 5: Implement fallback and interruption rules**

- Browser startup failures `unsupported`, `track_not_supported`, `network`, `language-not-supported`, and `service-not-allowed` switch once to local.
- Permission denial, missing track, and device-not-found are terminal for voice but leave text usable.
- While the call remains active, a natural `onRecognitionEnd` schedules one guarded restart after 150 ms. Reset the empty-run counter after any final transcript; after two consecutive recognition runs with no final transcript, cancel browser recognition and switch once to local.
- `onSpeechStart` during `speaking/responding/thinking` cancels browser/local playback, sends `turn.cancel`, clears sentence queue, and enters `user_speaking`.
- That interruption also sets `notice="interrupted"`; clear it with a generation-guarded 1,200 ms timer so the bottom bar can briefly show “你已打断声灵” without delaying the new turn.
- Local `vad.started` uses the same interruption path.
- Browser `onFinal` sends exactly one non-empty trimmed transcript and enters `thinking`.
- Server `asr.final` assigns the active voice turn ID; only deltas for that turn reach the sentence queue.
- A browser synthesis failure cancels the browser queue, switches the call once to local, and—after the matching `assistant.done`—sends one `assistant.speak` request with a monotonically increasing `request_id`. Its local audio events are then handled by the existing playback path.

- [ ] **Step 6: Run state-machine tests and typecheck**

```powershell
& $voxNode '.\frontend\node_modules\vitest\vitest.mjs' run --root frontend src/realtime/RealtimeVoiceEngine.test.ts
& $voxNode '.\frontend\node_modules\typescript\bin\tsc' --noEmit -p frontend/tsconfig.json
```

Expected: PASS, including stale-callback and double-fallback cases.

- [ ] **Step 7: Commit**

```powershell
git add frontend/src/realtime/RealtimeVoiceEngine.ts frontend/src/realtime/RealtimeVoiceEngine.test.ts
git commit -m "feat: add realtime voice state machine"
```

---

### Task 6: Wire the engine into the session hook and persisted settings

**Files:**
- Modify: `frontend/src/useVoiceSession.ts`
- Modify: `frontend/src/voiceSettings.ts`
- Modify: `frontend/src/__tests__/useVoiceSession.test.ts`
- Modify: `frontend/src/__tests__/voiceSettings.test.ts`

**Interfaces:**
- Consumes: `RealtimeVoiceEngine` and Task 4 protocol events.
- Produces controller fields: `realtime`, `microphones`, `selectedMicrophoneId`, `speechMode`, `browserVoices`, `selectedBrowserVoiceKey`.
- Produces controller methods: `startRealtimeCall`, `stopRealtimeCall`, `setSpeechMode`, `selectMicrophone`, `selectBrowserVoice`.

- [ ] **Step 1: Extend controller tests before its interface**

Test that:

```ts
await hook.result.current.startRealtimeCall();
expect(hook.result.current.realtime.active).toBe(true);
expect(hook.result.current.realtime.provider).toBe("browser");

act(() => browserRecognition.emitFinal("帮我总结文档"));
expect(socket.jsonMessages().at(-1)).toEqual({
  type: "voice.transcript.submit",
  text: "帮我总结文档",
});

act(() => hook.result.current.submitText("文字问题"));
expect(socket.jsonMessages().at(-1)).toEqual({
  type: "text.submit",
  text: "文字问题",
  speak_response: false,
});
```

Also test `asr.partial` updates only `realtime.interimText`, while `asr.final` creates one permanent user message. Verify that acknowledging the online-service notice persists the setting; starting `local-only` does not mutate that acknowledgement.

- [ ] **Step 2: Add failing settings migration tests**

Define:

```ts
export interface VoiceSettings {
  voiceKey: string | null;
  speed: VoiceSpeed;
  speechMode: "online-preferred" | "local-only";
  microphoneDeviceId: string | null;
  microphoneLabel: string | null;
  browserVoiceKey: string | null;
  onlineSpeechNoticeAccepted: boolean;
}
```

Old v1 JSON must migrate with `speechMode="online-preferred"`, null new identifiers, and `onlineSpeechNoticeAccepted=false`. Invalid modes/device IDs must fall back safely.

- [ ] **Step 3: Run hook/settings tests and verify type failures**

```powershell
& $voxNode '.\frontend\node_modules\vitest\vitest.mjs' run --root frontend src/__tests__/useVoiceSession.test.ts src/__tests__/voiceSettings.test.ts
```

Expected: FAIL because the controller and v2 settings fields do not exist.

- [ ] **Step 4: Add the controller surface and engine lifecycle**

Extend `VoiceSessionController` with:

```ts
realtime: RealtimeSnapshot;
microphones: MicrophoneDevice[];
selectedMicrophoneId: string | null;
speechMode: SpeechMode;
browserVoices: BrowserVoice[];
selectedBrowserVoiceKey: string | null;
onlineSpeechNoticeAccepted: boolean;
startRealtimeCall(): Promise<void>;
stopRealtimeCall(): Promise<void>;
setSpeechMode(mode: SpeechMode): void;
selectMicrophone(deviceId: string): void;
selectBrowserVoice(voiceKey: string): void;
acceptOnlineSpeechNotice(): void;
```

Create the engine once in a ref. Construct `MicrophoneCapture` with the existing socket binary-send callback, then pass the capture, browser adapter, sentence queue, socket JSON-send callback, snapshot callback, and terminal-error callback to the engine. `startRealtimeCall` computes `allowDefaultInputFallback` with `browserDefaultMatchesSelection` and passes it to the engine. Feed every parsed server event exactly once into `engine.handleServerEvent(event)` before existing UI handling; the engine itself filters `assistant.delta`, `assistant.done`, ASR, VAD, and local audio events by turn/request ID.

Keep the existing `speakMessage(turnId)` API. In `online-preferred`, look up the completed assistant message text, cancel any prior playback, and call browser `speak` with the selected browser voice; if it fails, send exactly one existing `assistant.speak` request for Kokoro. In `local-only`, send the Kokoro request immediately. `stopSpeaking` must cancel both browser synthesis and local playback/request state. Add hook tests for browser success, one-time local fallback, local-only behavior, and a stale browser `onend` after stop.

On WebSocket close, session reset, component unmount, or clear-local-data, await/trigger `engine.stop()` and reset the snapshot to `off`.

- [ ] **Step 5: Persist v2 settings without exposing private server IDs**

Rename storage key to `voxagent.voice-settings.v2`, migrate v1 once, and store only public voice keys, browser voice URI keys, mode, microphone ID/label, and the online-speech notice acknowledgement. Continue removing the legacy `voxagent.voice` key.

- [ ] **Step 6: Run hook regression and typecheck**

```powershell
& $voxNode '.\frontend\node_modules\vitest\vitest.mjs' run --root frontend src/__tests__/useVoiceSession.test.ts src/__tests__/voiceSettings.test.ts
& $voxNode '.\frontend\node_modules\typescript\bin\tsc' --noEmit -p frontend/tsconfig.json
```

Expected: PASS; all existing replay request-id, typed-silent, microphone cancellation, and connection tests remain green.

- [ ] **Step 7: Commit**

```powershell
git add frontend/src/useVoiceSession.ts frontend/src/voiceSettings.ts frontend/src/__tests__/useVoiceSession.test.ts frontend/src/__tests__/voiceSettings.test.ts
git commit -m "feat: integrate realtime voice session"
```

---

### Task 7: Add the approved call, device, status, and voice UI

**Files:**
- Create: `frontend/src/components/RealtimeVoiceControls.tsx`
- Create: `frontend/src/components/RealtimeVoiceControls.test.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/Composer.tsx`
- Modify: `frontend/src/components/VoicePicker.tsx`
- Modify: `frontend/src/components/VoiceStatus.tsx`
- Modify: `frontend/src/presentation.ts`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/__tests__/App.test.tsx`
- Modify: `frontend/src/__tests__/presentation.test.ts`

**Interfaces:**
- Consumes: Task 6 `VoiceSessionController` realtime fields/methods.
- Produces: accessible controls and approved bottom status copy.

- [ ] **Step 1: Write failing accessible control tests**

Require:

```ts
expect(screen.getByRole("button", { name: "开始实时通话" })).toBeVisible();
expect(screen.getByRole("combobox", { name: "麦克风" })).toHaveValue("realtek");
expect(screen.getByRole("option", { name: /ToDesk.*虚拟/ })).toBeVisible();
expect(screen.getByRole("radiogroup", { name: "语音模式" })).toBeVisible();
expect(screen.getByRole("meter", { name: "麦克风音量" })).toHaveAttribute("aria-valuenow", "42");
```

Clicking start/stop must call exactly `startRealtimeCall`/`stopRealtimeCall`. Device and mode changes call the corresponding controller methods.

- [ ] **Step 2: Write failing status/message tests**

Cover the exact Chinese labels:

```ts
expect(realtimeVoiceStatusPresentation("user_speaking")?.label).toBe("检测到你在说话");
expect(realtimeVoiceStatusPresentation("connecting")?.label).toBe("正在连接");
expect(realtimeVoiceStatusPresentation("listening")?.label).toBe("正在监听");
expect(realtimeVoiceStatusPresentation("transcribing")?.label).toBe("正在识别");
expect(realtimeVoiceStatusPresentation("responding")?.label).toBe("声灵正在回复");
expect(realtimeVoiceStatusPresentation("speaking")?.label).toBe("声灵正在朗读");
expect(realtimeVoiceStatusPresentation("fallback")?.label).toBe("已切换到本地语音");
expect(realtimeVoiceStatusPresentation("user_speaking", "interrupted")?.label).toBe("你已打断声灵");
```

Keep the existing `voiceStatusPresentation(VoiceStatus)` for one-shot preview/local replay. Add `realtimeVoiceStatusPresentation(state: RealtimeVoiceState, notice: RealtimeNotice = null)`: it returns the interruption label when `notice="interrupted"`, returns `null` for `off`, and maps every other realtime state to a complete label/icon record. `VoiceStatus` renders the realtime presentation whenever `realtime.state !== "off"` or a realtime notice exists; otherwise it renders the existing one-shot status.

An interim transcript must render as one right-side translucent bubble labelled “用户（识别中）” and disappear when the matching final user message arrives.

- [ ] **Step 3: Run component tests and verify missing UI failure**

```powershell
& $voxNode '.\frontend\node_modules\vitest\vitest.mjs' run --root frontend src/components/RealtimeVoiceControls.test.tsx src/__tests__/App.test.tsx src/__tests__/presentation.test.ts
```

Expected: FAIL because the controls/states are absent.

- [ ] **Step 4: Implement `RealtimeVoiceControls`**

Render:

- Start/end call button.
- `online-preferred` and `local-only` mode controls.
- Microphone `<select>` with “（虚拟）” suffix for virtual devices.
- `<meter min="0" max="1">` bound to `realtime.inputLevel`.
- Provider badge: “浏览器在线语音” or “本地离线语音”.
- Non-blocking “3 秒未检测到声音，请切换到 Realtek 麦克风” diagnostic.
- Before the first `online-preferred` start, an inline notice says the browser/platform may process microphone speech online. “同意并开始” persists acknowledgement and starts the call; “改用仅本地” changes mode without starting an online recognizer.

Do not hide text input while a call is active.

- [ ] **Step 5: Replace composer push-to-talk copy without removing text behavior**

`Composer` delegates call control to `RealtimeVoiceControls`; its textarea/send/escape behavior stays unchanged. Remove the old “松开后” copy and any requirement to click twice for a voice turn.

- [ ] **Step 6: Merge browser and local voices in `VoicePicker`**

Show browser Chinese voices first with provider text “在线/系统”，then the single Kokoro fallback with “本地·生成较慢”. Browser preview calls `selectBrowserVoice` plus browser TTS preview; local preview keeps the existing WebSocket request. Never display Melo or native voice IDs.

- [ ] **Step 7: Apply approved status and responsive styles**

Keep the status bar immediately above the composer at the bottom. Add visible focus states, 44 px mobile controls, reduced-motion compliance, a translucent interim bubble, and a meter that does not rely on color alone. Do not redesign the header, RAG panel, memory panel, or message identities.

- [ ] **Step 8: Run UI tests and build**

```powershell
& $voxNode '.\frontend\node_modules\vitest\vitest.mjs' run --root frontend src/components/RealtimeVoiceControls.test.tsx src/__tests__/App.test.tsx src/__tests__/presentation.test.ts
& $voxNode '.\frontend\node_modules\typescript\bin\tsc' --noEmit -p frontend/tsconfig.json
Push-Location frontend; & $voxNode '.\node_modules\vite\bin\vite.js' build; Pop-Location
```

Expected: PASS and Vite reports a successful production build.

- [ ] **Step 9: Commit**

```powershell
git add frontend/src/components/RealtimeVoiceControls.tsx frontend/src/components/RealtimeVoiceControls.test.tsx frontend/src/App.tsx frontend/src/components/Composer.tsx frontend/src/components/VoicePicker.tsx frontend/src/components/VoiceStatus.tsx frontend/src/presentation.ts frontend/src/styles.css frontend/src/__tests__/App.test.tsx frontend/src/__tests__/presentation.test.ts
git commit -m "feat: add realtime voice call controls"
```

---

### Task 8: Prove fallback, interruption, and end-to-end session safety

**Files:**
- Modify: `frontend/src/realtime/RealtimeVoiceEngine.test.ts`
- Modify: `frontend/src/__tests__/useVoiceSession.test.ts`
- Modify: `backend/tests/e2e/test_voice_loop.py`
- Modify: `docs/plan-02/README.md`

**Interfaces:**
- Consumes: all prior task interfaces.
- Produces: automated acceptance evidence and a target-machine manual acceptance checklist.

- [ ] **Step 1: Add cross-component race regression tests**

Cover these sequences:

1. Browser TTS is speaking → user `speechstart` → old `onend` arrives → state remains `user_speaking`.
2. Browser ASR network error → local PCM forwarding starts → late browser final arrives → no duplicate submit.
3. Local `asr.partial` → local `asr.final` → exactly one permanent user message.
4. Online voice turn active → typed text submitted → typed reply remains silent.
5. WebSocket reconnect → old engine callbacks cannot mutate the new session.
6. Browser voice unavailable → Kokoro fallback error leaves completed text visible and call returns to listening.

- [ ] **Step 2: Run the race regression tests**

```powershell
& $voxNode '.\frontend\node_modules\vitest\vitest.mjs' run --root frontend src/realtime/RealtimeVoiceEngine.test.ts src/__tests__/useVoiceSession.test.ts
```

Expected: PASS because the generation and request guards were implemented in Tasks 5–6. If a regression fails, stop this acceptance task and use systematic debugging plus a focused failing test in the owning unit before proceeding; do not weaken assertions.

- [ ] **Step 3: Add backend browser-transcript WebSocket E2E**

Send `voice.transcript.submit`, then assert:

```python
assert [event["type"] for event in events] == [
    "asr.final", "assistant.delta", "assistant.done"
]
assert audio == []
assert events[0]["text"] == "浏览器最终转写"
```

Then submit a local microphone turn in the same session and assert both turns appear in one ordered history with voice origins.

- [ ] **Step 4: Run all relevant automated verification**

```powershell
& '.\backend\.venv\Scripts\python.exe' -m pytest backend/tests/api backend/tests/conversation backend/tests/speech backend/tests/e2e -q -p no:cacheprovider --basetemp="$env:TEMP\voxagent-pytest-task-8"
& '.\backend\.venv\Scripts\python.exe' -m ruff check backend/src backend/tests
& $voxNode '.\frontend\node_modules\vitest\vitest.mjs' run --root frontend
& $voxNode '.\frontend\node_modules\typescript\bin\tsc' --noEmit -p frontend/tsconfig.json
Push-Location frontend; & $voxNode '.\node_modules\vite\bin\vite.js' build; Pop-Location
git diff --check
```

Expected: all relevant backend tests pass (model-dependent tests may only skip for their documented missing runtime condition), all frontend tests pass, Ruff passes, TypeScript passes, Vite builds, and `git diff --check` is clean.

- [ ] **Step 5: Update the runbook and privacy copy**

Document:

- How to select “麦克风阵列 (Realtek(R) Audio)”.
- Why ToDesk/XReal are marked virtual.
- Difference between “在线优先” and “仅本地”.
- Browser speech may use a platform online service and has no project API key.
- How automatic fallback is shown.
- Why local Kokoro is slower and Melo was removed.
- How to diagnose a flat input meter.
- Exact manual acceptance sequence below.

- [ ] **Step 6: Start the three local services without touching unrelated apps**

Only start/restart VoxAgent's Ollama, backend, and Vite. Do not stop GPT, Codex, Roxi, ToDesk, or NVIDIA processes. Reuse one session token in the backend command and browser URL.

- [ ] **Step 7: Perform target-machine manual acceptance**

Record pass/fail and measured times for:

1. Realtek selection persists after refresh.
2. Input meter changes within 100 ms while speaking.
3. Three Mandarin phrases show interim and final text.
4. A pause auto-submits without a second click.
5. Browser Chinese voice speaks two streamed sentences clearly and in order.
6. Speaking over Agent stops output within 300 ms and old output never resumes.
7. Simulated browser recognition failure switches to local mode and completes one turn.
8. Typed input remains silent in both modes.

Physical microphone and audible quality require the user to speak/listen; do not mark them passed from synthetic tests.

- [ ] **Step 8: Commit final acceptance and documentation**

```powershell
git add frontend/src/realtime/RealtimeVoiceEngine.test.ts frontend/src/__tests__/useVoiceSession.test.ts backend/tests/e2e/test_voice_loop.py docs/plan-02/README.md
git commit -m "test: verify hybrid realtime voice fallback"
```

---

## Final Review Gate

After Task 8:

1. Request a specification-compliance review against `docs/superpowers/specs/2026-09-05-hybrid-realtime-voice-design.md`.
2. Fix every Critical/Important finding with a failing test first.
3. Rerun the full verification command set from Task 8 Step 4.
4. Use `superpowers:verification-before-completion` before reporting success.
5. Use `superpowers:finishing-a-development-branch` to offer merge/push/worktree choices; do not push or merge without the user's instruction.
