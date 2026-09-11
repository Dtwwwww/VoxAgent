# 文本输入静默回复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 文本框发送消息时固定只返回文字，同时保留语音输入自动语音回复和 Agent 消息手动朗读。

**Architecture:** 将“文本回复是否自动朗读”从用户偏好改为固定的交互策略：前端所有 `text.submit` 都发送 `speak_response: false`。删除 UI、控制器和 localStorage 中的自动朗读设置；后端语音输入与 `assistant.speak` 路径保持不变。

**Tech Stack:** React 19、TypeScript、Vitest、Testing Library、Vite

## Global Constraints

- 文本输入始终使用 `speak_response: false`。
- 语音输入继续由后端以 `speak_response=True` 生成语音回复。
- Agent 消息的“朗读”按钮继续发送 `assistant.speak`，且支持重复播放。
- localStorage 仅持久化公开的 `voiceKey` 和 `speed`。
- 不修改后端协议或编排器。
- 仅运行相关前端测试、TypeScript 类型检查和一次 Vite 构建。

---

### Task 1: 固定文本静默策略并移除自动朗读设置

**Files:**
- Modify: `frontend/src/components/Composer.tsx`
- Modify: `frontend/src/useVoiceSession.ts`
- Modify: `frontend/src/voiceSettings.ts`
- Test: `frontend/src/__tests__/App.test.tsx`
- Test: `frontend/src/__tests__/useVoiceSession.test.ts`
- Test: `frontend/src/__tests__/voiceSettings.test.ts`

**Interfaces:**
- Consumes: 现有 WebSocket 事件 `{ type: "text.submit", text: string, speak_response: boolean }` 与 `{ type: "assistant.speak", turn_id: number }`。
- Produces: `VoiceSessionController.submitText(text)` 永远发送 `speak_response: false`；`VoiceSettings` 只包含 `voiceKey` 和 `speed`。

- [ ] **Step 1: 先写 UI 和文字发送失败测试**

在 `frontend/src/__tests__/App.test.tsx` 中从模拟控制器移除 `speakTextReplies` 与 `setSpeakTextReplies`，并在现有界面测试中加入：

```tsx
expect(screen.queryByRole("checkbox", { name: "文字回复自动朗读" })).toBeNull();
expect(screen.getAllByRole("button", { name: "朗读" })).toHaveLength(1);
```

在 `frontend/src/__tests__/useVoiceSession.test.ts` 中把文本提交测试改成：先写入旧的自动朗读配置，再打开会话并连续提交两次文字，两次都必须静默：

```ts
localStorage.setItem("voxagent.voice-settings.v1", JSON.stringify({
  voiceKey: null,
  speed: 1,
  speakTextReplies: true,
}));
const { hook, socket } = openSession();

act(() => hook.result.current.submitText(" hello "));
expect(socket.jsonMessages().at(-1)).toEqual({
  type: "text.submit",
  text: "hello",
  speak_response: false,
});

act(() => hook.result.current.submitText("again"));
expect(socket.jsonMessages().at(-1)).toEqual({
  type: "text.submit",
  text: "again",
  speak_response: false,
});
```

- [ ] **Step 2: 写设置兼容性失败测试**

在 `frontend/src/__tests__/voiceSettings.test.ts` 中把默认设置与旧数据读取断言改为只包含音色和语速：

```ts
const defaults = { voiceKey: null, speed: 1.0 as const };

storage.set("voxagent.voice-settings.v1", JSON.stringify({
  voiceKey: "default_voice",
  speed: 9,
  speakTextReplies: true,
  token: "secret",
}));
expect(loadVoiceSettings(api)).toEqual({ voiceKey: "default_voice", speed: 1.0 });

saveVoiceSettings(api, { voiceKey: "default_voice", speed: 0.8 });
expect(JSON.parse(storage.get("voxagent.voice-settings.v1")!)).toEqual({
  voiceKey: "default_voice",
  speed: 0.8,
});
```

- [ ] **Step 3: 运行聚焦测试并确认正确红灯**

Run:

```powershell
& .\node_modules\.bin\vitest.CMD run src\__tests__\App.test.tsx src\__tests__\useVoiceSession.test.ts src\__tests__\voiceSettings.test.ts
```

Expected: FAIL；页面仍存在复选框，旧 `speakTextReplies: true` 仍会让第二次文字提交携带 `speak_response: true`，设置对象仍包含该字段。

- [ ] **Step 4: 实现固定的文本静默策略**

在 `frontend/src/components/Composer.tsx` 删除自动朗读标签：

```tsx
// 删除这一整个元素：
// <label className="auto-speak">...</label>
```

在 `frontend/src/useVoiceSession.ts`：

```ts
// 从 VoiceSessionController 删除：
// speakTextReplies: boolean;
// setSpeakTextReplies(enabled: boolean): void;

// 删除 speakTextReplies 状态和 setSpeakTextReplies 回调。
// voices.available 只协调 voiceKey 与 speed。
const settings = reconcileVoiceSettings({
  voiceKey: current?.voiceKey ?? null,
  speed: current?.speed ?? 1,
}, event.voices);

// 文字发送固定静默。
send({ type: "text.submit", text, speak_response: false });

// 选择音色时只保存公开音色设置。
saveVoiceSettings(localStorage, { voiceKey, speed });
```

在 `frontend/src/voiceSettings.ts` 将设置模型收窄：

```ts
export interface VoiceSettings {
  voiceKey: string | null;
  speed: VoiceSpeed;
}

const defaults: VoiceSettings = { voiceKey: null, speed: 1.0 };

return {
  voiceKey: typeof value.voiceKey === "string" ? value.voiceKey : null,
  speed: speed(value.speed),
};

storage.setItem(VOICE_SETTINGS_KEY, JSON.stringify({
  voiceKey: settings.voiceKey,
  speed: speed(settings.speed),
}));
```

- [ ] **Step 5: 运行聚焦测试并确认绿灯**

Run:

```powershell
& .\node_modules\.bin\vitest.CMD run src\__tests__\App.test.tsx src\__tests__\useVoiceSession.test.ts src\__tests__\voiceSettings.test.ts
```

Expected: 3 个测试文件全部 PASS，0 failures。

- [ ] **Step 6: 类型检查与生产构建**

Run:

```powershell
& .\node_modules\.bin\tsc.CMD --noEmit
```

Expected: exit code 0。

Run:

```powershell
& .\node_modules\.bin\vite.CMD build
```

Expected: exit code 0，生成 `dist/`。

- [ ] **Step 7: 提交实现**

```powershell
git add -- frontend/src/components/Composer.tsx frontend/src/useVoiceSession.ts frontend/src/voiceSettings.ts frontend/src/__tests__/App.test.tsx frontend/src/__tests__/useVoiceSession.test.ts frontend/src/__tests__/voiceSettings.test.ts
git commit -m "fix: keep typed replies silent"
```
