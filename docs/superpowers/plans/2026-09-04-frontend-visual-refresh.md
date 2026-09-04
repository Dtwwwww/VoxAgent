# VoxAgent Frontend Visual Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the current utilitarian VoxAgent Web client into a polished, friendly, responsive local-AI chat interface while preserving its text and voice behavior.

**Architecture:** Keep `useVoiceSession` as the single owner of connection and voice state. Split presentation into focused React components that consume `VoiceSessionController`, use small pure presentation helpers for status/error mapping, and centralize the visual system in native CSS custom properties. Add only the minimum controller state needed to expose active preview and active spoken-message identity to the interface; do not change the WebSocket protocol.

**Tech Stack:** React 19, TypeScript 7, Vite 8, Vitest 4, Testing Library, native CSS, Web Audio API.

## Global Constraints

- Work in `.worktrees/phase-02-voice-loop`; preserve all existing uncommitted voice fixes.
- Do not modify backend files, protocol event shapes, model configuration, or TTS/ASR selection.
- Do not add npm packages or network-loaded fonts/assets.
- Typed input must continue sending `speak_response: false`; voice input may auto-play its response.
- The visible user name is exactly `用户`; the assistant product name is exactly `声灵`.
- `voice-005` remains the default visible voice; `engine-002`, `engine-003`, and `engine-004` remain equal candidates when provided by the server.
- Emoji must not become a separate message or be read by TTS; preserve the existing speech sanitization behavior.
- Minimum pointer target is 44×44px on mobile; keyboard focus must remain visible.
- Respect `prefers-reduced-motion: reduce`.
- Run focused tests after every task; run the full frontend test, typecheck, and build commands before completion.

---

## File Map

**Create:**

- `frontend/src/components/Icon.tsx` — dependency-free inline SVG icon set.
- `frontend/src/components/Header.tsx` — brand, connection state, and voice-picker trigger.
- `frontend/src/components/Conversation.tsx` — empty state, message list, scroll ownership, return-to-bottom control.
- `frontend/src/components/VoiceStatus.tsx` — near-composer voice/connection state feedback.
- `frontend/src/components/ErrorNotice.tsx` — error classification and recovery action.
- `frontend/src/presentation.ts` — pure label and error presentation functions.
- `frontend/src/__tests__/presentation.test.ts` — pure presentation tests.

**Modify:**

- `frontend/src/App.tsx` — compose the new application shell and own picker-open state.
- `frontend/src/components/ChatMessage.tsx` — polished left/right message presentation and message actions.
- `frontend/src/components/Composer.tsx` — two-row composer, IME-safe submission, microphone state, and status placement.
- `frontend/src/components/VoicePicker.tsx` — accessible modal/popover and mutually exclusive preview controls.
- `frontend/src/useVoiceSession.ts` — expose `speakingTurnId`, `previewingVoiceKey`, `stopSpeaking`, and `stopVoicePreview` without protocol changes.
- `frontend/src/styles.css` — complete tokenized visual system, responsive layout, state animation, and reduced-motion rules.
- `frontend/src/__tests__/App.test.tsx` — component and accessibility behavior tests.
- `frontend/src/__tests__/useVoiceSession.test.ts` — controller state tests for replay/preview stop actions.

---

### Task 1: Lock Presentation Rules with Pure Helpers

**Files:**
- Create: `frontend/src/presentation.ts`
- Create: `frontend/src/__tests__/presentation.test.ts`

**Interfaces:**
- Consumes: `ConnectionStatus`, `SessionError`, and `VoiceStatus` from `useVoiceSession.ts`.
- Produces: `connectionPresentation()`, `voiceStatusPresentation()`, and `errorPresentation()`.

- [x] **Step 1: Write failing tests for exact labels and recovery kinds**

```ts
import { describe, expect, it } from "vitest";
import { connectionPresentation, errorPresentation, voiceStatusPresentation } from "../presentation";

describe("presentation", () => {
  it("describes local connection states", () => {
    expect(connectionPresentation("connected", "qwen2.5:7b").label).toBe("本地运行 · qwen2.5:7b");
    expect(connectionPresentation("connecting", null).label).toBe("正在连接本地服务");
    expect(connectionPresentation("disconnected", null).label).toBe("本地服务未连接");
  });

  it("keeps idle visually empty and maps active voice states", () => {
    expect(voiceStatusPresentation("idle")).toBeNull();
    expect(voiceStatusPresentation("listening")?.label).toBe("正在聆听，点击停止");
    expect(voiceStatusPresentation("thinking")?.label).toBe("声灵正在思考");
  });

  it("classifies connection, microphone, and playback errors", () => {
    expect(errorPresentation({ code: "connection", message: "raw", recoverable: true })).toMatchObject({ action: "connect", label: "本地服务未启动，请启动后重试。" });
    expect(errorPresentation({ code: "microphone_permission", message: "raw", recoverable: true })).toMatchObject({ action: "microphone", label: "无法使用麦克风，请在浏览器地址栏中允许麦克风权限。" });
    expect(errorPresentation({ code: "audio_playback", message: "raw", recoverable: true })).toMatchObject({ action: "none", label: "浏览器阻止了自动播放，请点击朗读。" });
  });
});
```

- [x] **Step 2: Run the focused test and confirm it fails because the module is absent**

Run: `pnpm --dir frontend test -- --run src/__tests__/presentation.test.ts`  
Expected: FAIL with module resolution error for `../presentation`.

- [x] **Step 3: Implement typed presentation helpers**

```ts
import type { ConnectionStatus, SessionError, VoiceStatus } from "./useVoiceSession";

export type Tone = "neutral" | "success" | "warning" | "danger";
export type RecoveryAction = "none" | "connect" | "microphone";

export function connectionPresentation(status: ConnectionStatus, modelId: string | null) {
  if (status === "connected") return { tone: "success" as const, label: `本地运行 · ${modelId ?? "本地模型"}` };
  if (status === "connecting") return { tone: "warning" as const, label: "正在连接本地服务" };
  return { tone: "danger" as const, label: "本地服务未连接" };
}

export function voiceStatusPresentation(status: VoiceStatus) {
  const values = {
    listening: { icon: "wave", label: "正在聆听，点击停止" },
    transcribing: { icon: "spinner", label: "正在识别你的语音" },
    thinking: { icon: "thinking", label: "声灵正在思考" },
    speaking: { icon: "wave", label: "正在回复" },
  } as const;
  return status === "idle" ? null : values[status];
}

export function errorPresentation(error: SessionError) {
  if (error.code === "connection") return { label: "本地服务未启动，请启动后重试。", action: "connect" as const };
  if (error.code === "microphone_permission") return { label: "无法使用麦克风，请在浏览器地址栏中允许麦克风权限。", action: "microphone" as const };
  if (error.code === "audio_playback") return { label: "浏览器阻止了自动播放，请点击朗读。", action: "none" as const };
  if (error.code === "preview_failed") return { label: "音色试听失败，请选择其他音色。", action: "none" as const };
  return { label: error.message, action: error.recoverable ? "connect" as const : "none" as const };
}
```

- [x] **Step 4: Run the focused test**

Run: `pnpm --dir frontend test -- --run src/__tests__/presentation.test.ts`  
Expected: PASS, 3 tests.

- [x] **Step 5: Commit the helper and its tests**

```powershell
git add frontend/src/presentation.ts frontend/src/__tests__/presentation.test.ts
git commit -m "feat: add frontend presentation rules"
```

---

### Task 2: Expose Playback Identity and Stop Actions

**Files:**
- Modify: `frontend/src/useVoiceSession.ts`
- Modify: `frontend/src/__tests__/useVoiceSession.test.ts`

**Interfaces:**
- Consumes: existing `AudioPlayback.stopTurn()` and `AudioPlayback.stopPreview()`.
- Produces on `VoiceSessionController`: `speakingTurnId: number | null`, `previewingVoiceKey: string | null`, `stopSpeaking(turnId: number): void`, `stopVoicePreview(): void`.

- [x] **Step 1: Add compile-level fixture fields and failing behavioral tests**

Add the four interface members to the test controller fixtures. Add tests that dispatch a turn TTS chunk or invoke a preview, then assert active identity and stop behavior:

```ts
expect(result.current.previewingVoiceKey).toBe("default_voice");
act(() => result.current.stopVoicePreview());
expect(result.current.previewingVoiceKey).toBeNull();

act(() => result.current.speakMessage(3));
expect(sentJson(socket).at(-1)).toEqual({ type: "assistant.speak", turn_id: 3 });
act(() => result.current.stopSpeaking(3));
expect(result.current.speakingTurnId).toBeNull();
```

- [x] **Step 2: Run the focused controller tests and confirm failure**

Run: `pnpm --dir frontend test -- --run src/__tests__/useVoiceSession.test.ts`  
Expected: FAIL because the new state and actions do not exist.

- [x] **Step 3: Implement the minimal controller state**

Add state:

```ts
const [speakingTurnId, setSpeakingTurnId] = useState<number | null>(null);
const [previewingVoiceKey, setPreviewingVoiceKey] = useState<string | null>(null);
```

Update turn audio consumption to set `speakingTurnId` from metadata, set preview identity when `previewVoice()` successfully sends, and clear both identities in cancellation, disconnect, and local-resource cleanup. Implement actions:

```ts
const stopSpeaking = useCallback((turnId: number) => {
  playbackRef.current.stopTurn(turnId);
  setSpeakingTurnId((current) => current === turnId ? null : current);
  setVoiceStatus("idle");
}, []);

const stopVoicePreview = useCallback(() => {
  if (pendingAudioRef.current?.kind === "preview") pendingAudioRef.current.valid = false;
  playbackRef.current.stopPreview();
  setPreviewingVoiceKey(null);
  setVoiceStatus("idle");
}, []);
```

Return all four additions from the hook. Do not send any new WebSocket event.

- [x] **Step 4: Run controller tests and typecheck**

Run: `pnpm --dir frontend test -- --run src/__tests__/useVoiceSession.test.ts`  
Expected: PASS.  
Run: `pnpm --dir frontend typecheck`  
Expected: PASS after every `VoiceSessionController` fixture includes the new members.

- [x] **Step 5: Commit the controller presentation API**

```powershell
git add frontend/src/useVoiceSession.ts frontend/src/__tests__/useVoiceSession.test.ts frontend/src/__tests__/App.test.tsx
git commit -m "feat: expose voice playback state to interface"
```

---

### Task 3: Build the Application Shell and Conversation Experience

**Files:**
- Create: `frontend/src/components/Icon.tsx`
- Create: `frontend/src/components/Header.tsx`
- Create: `frontend/src/components/Conversation.tsx`
- Modify: `frontend/src/components/ChatMessage.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/__tests__/App.test.tsx`

**Interfaces:**
- `Icon({ name, size?, className? })` supports `spark`, `microphone`, `stop`, `send`, `copy`, `volume`, `chevronDown`, `arrowDown`, `settings`, `close`, `spinner`, and `wave`.
- `Header({ controller, onOpenVoices })` shows brand, connection presentation, and selected voice.
- `Conversation({ messages, speakingTurnId, onSpeak, onStopSpeaking, onSuggestion })` owns scrolling and renders messages.

- [x] **Step 1: Replace the App test with assertions for the approved shell**

Cover exact product copy and message semantics:

```ts
expect(screen.getByRole("heading", { name: "声灵" })).toBeVisible();
expect(screen.getByText("你的本地语音 AI 助手")).toBeVisible();
expect(screen.getByText("本地运行 · qwen")).toBeVisible();
expect(screen.getByLabelText("声灵消息")).toHaveAttribute("data-side", "left");
expect(screen.getByLabelText("用户消息")).toHaveAttribute("data-side", "right");
expect(screen.getByRole("button", { name: /音色：/ })).toBeVisible();
```

Add an empty-controller test for the welcome copy and suggestions, and assert that clicking “介绍一下你自己” calls `submitText("介绍一下你自己")`.

- [x] **Step 2: Run the App test and confirm the new expectations fail**

Run: `pnpm --dir frontend test -- --run src/__tests__/App.test.tsx`  
Expected: FAIL on missing heading copy, empty state, and shell controls.

- [x] **Step 3: Implement the icon, header, conversation, and message components**

Use inline SVG with `aria-hidden="true"` inside already-labelled buttons. `Conversation` must use a scroll container ref and this bottom threshold:

```ts
const nearBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 96;
```

Only auto-scroll when `nearBottom` is true. Otherwise show a 44px “回到底部” button. `ChatMessage` must render “声灵” for assistant and “用户” for user, keep the “语音输入” and “已停止” tags, show copy for all messages, and show “朗读” or “停止朗读” only for complete assistant messages with a turn id.

- [x] **Step 4: Compose the shell in `App.tsx`**

Keep the stable lifecycle dependencies:

```ts
useEffect(() => {
  connect();
  return () => { void disconnect(); };
}, [connect, disconnect]);
```

Own `voicePickerOpen` in `App`, render `Header`, `Conversation`, `ErrorNotice`, `VoiceStatus`, `Composer`, and conditionally `VoicePicker`. Do not render the previous standalone sticky status footer.

- [x] **Step 5: Run focused tests and typecheck**

Run: `pnpm --dir frontend test -- --run src/__tests__/App.test.tsx`  
Expected: PASS.  
Run: `pnpm --dir frontend typecheck`  
Expected: PASS.

- [x] **Step 6: Commit the shell and conversation work**

```powershell
git add frontend/src/App.tsx frontend/src/components/Icon.tsx frontend/src/components/Header.tsx frontend/src/components/Conversation.tsx frontend/src/components/ChatMessage.tsx frontend/src/__tests__/App.test.tsx
git commit -m "feat: redesign chat shell and conversation"
```

---

### Task 4: Redesign Composer, Status, Errors, and Voice Picker

**Files:**
- Create: `frontend/src/components/VoiceStatus.tsx`
- Create: `frontend/src/components/ErrorNotice.tsx`
- Modify: `frontend/src/components/Composer.tsx`
- Modify: `frontend/src/components/VoicePicker.tsx`
- Modify: `frontend/src/__tests__/App.test.tsx`

**Interfaces:**
- `VoiceStatus({ connectionStatus, voiceStatus })` renders nothing only when connected and idle.
- `ErrorNotice({ error, onConnect, onMicrophone })` uses `errorPresentation` and shows one valid recovery action.
- `Composer({ controller })` remains controller-backed and adds IME-safe keyboard behavior.
- `VoicePicker({ controller, open, onClose })` is an accessible dialog.

- [x] **Step 1: Add failing interaction tests**

Test these exact behaviors:

```ts
fireEvent.compositionStart(input);
fireEvent.keyDown(input, { key: "Enter" });
expect(session.submitText).not.toHaveBeenCalled();
fireEvent.compositionEnd(input);

expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
expect(screen.getByText("声灵正在思考")).toBeVisible();

fireEvent.click(screen.getByRole("button", { name: /音色：/ }));
expect(screen.getByRole("dialog", { name: "选择音色" })).toBeVisible();
fireEvent.keyDown(document, { key: "Escape" });
expect(screen.queryByRole("dialog", { name: "选择音色" })).toBeNull();
```

Also assert that a `connection` error shows “重试连接”, a microphone permission error shows “重试麦克风”, and only one preview action can be active.

- [x] **Step 2: Run the App test and confirm interaction failures**

Run: `pnpm --dir frontend test -- --run src/__tests__/App.test.tsx`  
Expected: FAIL on IME handling, status presentation, error action, and dialog behavior.

- [x] **Step 3: Implement the composer and status row**

Track composition with a ref and submit only when not composing:

```ts
const composing = useRef(false);
if (event.key === "Enter" && !event.shiftKey && !composing.current) {
  event.preventDefault();
  submit();
}
```

The microphone button label must be “开始语音输入” when idle and “结束录音” while active. Disable it during `transcribing` and `thinking`. Show `cancelActive` as “停止生成” only during `thinking` or `speaking`. Keep the 1–4000 character validation and clear it after a valid submission.

- [x] **Step 4: Implement recoverable error actions**

`ErrorNotice` calls `controller.connect()` only for connection recovery and `controller.startMicrophone()` only for microphone recovery. It does not invent a generic retry for playback or non-recoverable errors.

- [x] **Step 5: Implement the accessible voice dialog**

Render only when `open` is true. Use `role="dialog"`, `aria-modal="true"`, `aria-labelledby="voice-picker-title"`, close on Escape and overlay click, and restore focus to the trigger after `onClose`. Label speeds exactly “舒缓”“自然”“稍快”. For the active preview voice, show “停止试听” and call `stopVoicePreview`; disable preview buttons on other voices until stopped.

- [x] **Step 6: Run focused tests and typecheck**

Run: `pnpm --dir frontend test -- --run src/__tests__/App.test.tsx`  
Expected: PASS.  
Run: `pnpm --dir frontend typecheck`  
Expected: PASS.

- [x] **Step 7: Commit the interaction redesign**

```powershell
git add frontend/src/components/VoiceStatus.tsx frontend/src/components/ErrorNotice.tsx frontend/src/components/Composer.tsx frontend/src/components/VoicePicker.tsx frontend/src/__tests__/App.test.tsx
git commit -m "feat: polish voice and composer interactions"
```

---

### Task 5: Apply the Responsive Visual System

**Files:**
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/__tests__/App.test.tsx`

**Interfaces:**
- Consumes the class names introduced in Tasks 3 and 4.
- Produces the approved warm-white visual system at desktop, tablet, and mobile widths.

- [ ] **Step 1: Add structural class assertions before CSS replacement**

Assert stable styling hooks rather than computed pixel values:

```ts
expect(screen.getByRole("main")).toHaveClass("app-shell");
expect(screen.getByLabelText("对话记录")).toHaveClass("conversation");
expect(screen.getByLabelText("消息输入")).toHaveClass("composer");
expect(screen.getByRole("status")).toHaveAttribute("data-state", "thinking");
```

- [ ] **Step 2: Replace `styles.css` with tokenized styles**

Begin with these exact tokens and derive component styles from them:

```css
:root {
  color: #20212a;
  background: #f6f7fb;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  --color-page: #f6f7fb;
  --color-surface: #ffffff;
  --color-primary: #6c63e8;
  --color-primary-soft: #efeeff;
  --color-text: #20212a;
  --color-text-soft: #737687;
  --color-success: #2eaf72;
  --color-warning: #e89b35;
  --color-danger: #e25555;
  --color-border: #e8e9f0;
  --radius-shell: 24px;
  --radius-message: 18px;
  --radius-control: 12px;
}
```

Implement a 960px desktop shell, independently scrolling conversation, fixed shell header/composer, 72%/68% assistant/user message widths, 44px mobile controls, dialog-to-bottom-sheet adaptation at 600px, visible focus states, hover states only under `(hover: hover)`, safe-area bottom padding, and text wrapping with `overflow-wrap: anywhere`.

- [ ] **Step 3: Add approved state animations**

Use 150–220ms transitions, 180ms message/dialog entrance, a 1.4s listening pulse, thinking dots, and speaking bars. Add:

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    scroll-behavior: auto !important;
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}
```

- [ ] **Step 4: Run focused tests, typecheck, and build**

Run: `pnpm --dir frontend test -- --run src/__tests__/App.test.tsx`  
Expected: PASS.  
Run: `pnpm --dir frontend typecheck`  
Expected: PASS.  
Run: `pnpm --dir frontend build`  
Expected: PASS and `frontend/dist/` created.

- [ ] **Step 5: Commit the responsive visual system**

```powershell
git add frontend/src/styles.css frontend/src/__tests__/App.test.tsx
git commit -m "style: apply responsive VoxAgent visual system"
```

---

### Task 6: Final Regression and Visual Acceptance

**Files:**
- Modify only files required to fix regressions found by this task.

**Interfaces:**
- Validates the complete frontend against the design document and current voice behavior.

- [ ] **Step 1: Run the complete frontend suite**

Run: `pnpm --dir frontend test -- --run`  
Expected: all frontend tests PASS with no unhandled errors.

- [ ] **Step 2: Run static and production checks**

Run: `pnpm --dir frontend typecheck`  
Expected: PASS.  
Run: `pnpm --dir frontend build`  
Expected: PASS.

- [ ] **Step 3: Start the existing application and inspect desktop layout**

Use the repository's existing launch command documented in its README, open the generated tokenized URL, and inspect at 1440×900. Verify: header alignment, left/right message identity, scroll behavior, status immediately above composer, voice dialog placement, long-text wrapping, and no content hidden under the composer.

- [ ] **Step 4: Inspect mobile layout**

Inspect at 390×844. Verify: 88–90% message width, 44px controls, bottom-sheet voice picker, keyboard-safe composer, no horizontal scroll, and visible focus/pressed states.

- [ ] **Step 5: Exercise the two interaction paths**

Typed path: submit text and confirm the response remains silent until “朗读” is clicked.  
Voice path: start/stop microphone and confirm visible listening → transcribing → thinking → speaking transitions. Confirm that manual replay works twice on the same completed assistant message.

- [ ] **Step 6: Record final evidence and commit only necessary fixes**

If verification required code corrections, add only those files and commit:

```powershell
git add frontend
git commit -m "fix: resolve frontend visual acceptance issues"
```

If no correction was required, do not create an empty commit. Report the exact passing commands, tested viewport sizes, and any environment-dependent voice limitation.
