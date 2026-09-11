# 本地实时语音连续播放优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将实时通话默认切换为本地模式，并在保持流式低延迟的前提下合并过短语音片段，减少断裂和停顿。

**Architecture:** 保持 WebSocket、后端 SenseVoice/VAD、Ollama 和 Kokoro 接口不变。前端设置层负责本地模式默认/迁移，`StreamingSentenceQueue` 负责在强边界、最小片段长度和最大等待长度之间合并文本，`RealtimeVoiceEngine` 继续负责取消、打断和回放生命周期。

**Tech Stack:** React 19、TypeScript 7、Vitest、Web Audio API、WebSocket、FastAPI、SenseVoice、Ollama、Kokoro。

## Global Constraints

- 实时通话默认 `local-only`；在线 Web Speech 仅作为手动备用。
- 原始麦克风音频不落盘；只保留设备 ID、名称和模式设置。
- 文字输入继续使用 `speak_response: false`，不自动发声。
- 不更换 Kokoro 模型、不修改在线识别算法、不改造后端 LLM/RAG/记忆逻辑。
- 取消、打断、切换模式和 WebSocket 断开必须清空待播缓冲，旧片段不得进入新回合。
- 保留现有工作树中的 `.token_tmp`、`node_modules/` 和 pytest 临时目录，不加入 Git。

---

### Task 1: 将本地模式设为默认并迁移旧设置

**Files:**
- Modify: `frontend/src/voiceSettings.ts`
- Test: `frontend/src/__tests__/voiceSettings.test.ts`

**Interfaces:**
- `loadVoiceSettings(storage: StorageLike): VoiceSettings` 返回 `speechMode: "local-only"` 作为默认，并将已保存的 `online-preferred` 迁移为 `local-only` 一次。
- `saveVoiceSettings` 继续只写公开设置字段。

- [ ] **Step 1: 更新失败测试期望**

将测试文件中的 `defaults.speechMode` 改为 `"local-only"`，并新增：

```ts
it("migrates an existing online preference to local-only", () => {
  const storage = new Map<string, string>([[VOICE_SETTINGS_KEY, JSON.stringify({
    ...defaults,
    speechMode: "online-preferred",
  })]]);
  const api = {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
  };

  expect(loadVoiceSettings(api).speechMode).toBe("local-only");
  expect(JSON.parse(storage.get(VOICE_SETTINGS_KEY)!)).toMatchObject({ speechMode: "local-only" });
});
```

- [ ] **Step 2: 运行设置测试确认红灯**

运行：

```powershell
& '.\\node_modules\\.bin\\vitest.cmd' run src/__tests__/voiceSettings.test.ts
```

预期：因生产默认仍为 `online-preferred`，新增迁移断言失败。

- [ ] **Step 3: 实现最小迁移**

将 `defaults.speechMode` 改为 `"local-only"`。在 `parseSettings` 返回对象前，将 `value.speechMode === "online-preferred"` 规范化为 `"local-only"`；`local-only` 保持不变，其他值也回退为 `local-only`。解析后调用既有 `saveVoiceSettings` 将迁移结果持久化。

- [ ] **Step 4: 运行设置测试确认绿灯**

运行同一 Vitest 命令，预期全部通过。

- [ ] **Step 5: 提交设置变更**

```powershell
git add frontend/src/voiceSettings.ts frontend/src/__tests__/voiceSettings.test.ts
git commit -m "feat: prefer local realtime speech mode"
```

### Task 2: 为流式分句增加短片段合并

**Files:**
- Modify: `frontend/src/realtime/sentenceQueue.ts`
- Test: `frontend/src/realtime/sentenceQueue.test.ts`

**Interfaces:**
- `StreamingSentenceQueue.pushSegments(delta: string): StreamingSpeechSegment[]` 保持签名不变。
- 强边界仍可立即触发；短边界片段会留在队列中，直到达到最小长度或最大长度。

- [ ] **Step 1: 添加合并行为的失败测试**

在现有测试中新增：

```ts
it("merges short adjacent sentences before emitting speech", () => {
  const queue = new StreamingSentenceQueue();

  expect(queue.pushSegments("你好。" )).toEqual([]);
  expect(queue.pushSegments("我是声灵。" )).toEqual([
    { text: "你好。我是声灵。", sourceCodePoints: 8 },
  ]);
});

it("flushes a short tail at assistant.done", () => {
  const queue = new StreamingSentenceQueue();

  expect(queue.push("好的。" )).toEqual([]);
  expect(queue.flush()).toEqual(["好的。"]);
});
```

- [ ] **Step 2: 运行分句测试确认红灯**

```powershell
& '.\\node_modules\\.bin\\vitest.cmd' run src/realtime/sentenceQueue.test.ts
```

预期：新增短句断言失败，因为当前强标点会立即输出。

- [ ] **Step 3: 实现最小合并规则**

在 `sentenceQueue.ts` 定义常量：

```ts
const MIN_SPEECH_CODE_POINTS = 12;
const MAX_SPEECH_CODE_POINTS = 28;
```

解析到强边界时先把文本追加到当前待播缓冲；仅当累计 Unicode code point 数达到 `MIN_SPEECH_CODE_POINTS` 时输出。若累计达到 `MAX_SPEECH_CODE_POINTS`，即使没有强边界也输出安全片段。`flushSegments()` 必须无条件输出剩余可朗读文本。所有长度计算使用 `Array.from`，保留 emoji/代理对安全和既有隐藏 Markdown/URL 规范化映射。

- [ ] **Step 4: 运行分句测试确认绿灯**

```powershell
& '.\\node_modules\\.bin\\vitest.cmd' run src/realtime/sentenceQueue.test.ts
```

预期：原有 URL、Markdown、emoji、取消和长文本测试，以及新增合并测试全部通过。

- [ ] **Step 5: 提交分句变更**

```powershell
git add frontend/src/realtime/sentenceQueue.ts frontend/src/realtime/sentenceQueue.test.ts
git commit -m "feat: merge short realtime speech segments"
```

### Task 3: 验证实时引擎生命周期与本地模式集成

**Files:**
- Verify: `frontend/src/realtime/RealtimeVoiceEngine.ts`
- Test: `frontend/src/realtime/RealtimeVoiceEngine.test.ts`
- Test: `frontend/src/__tests__/useVoiceSession.test.ts`

**Interfaces:**
- 引擎继续通过 `sentenceQueue.pushSegments`、`flushSegments`、`cancel` 和 `reset` 管理浏览器语音；本地模式仍由后端 PCM/VAD/ASR 路径处理。
- 取消、模式切换和 `stop()` 必须丢弃尚未播放的合并缓冲；现有测试已经覆盖这些生命周期边界。

- [ ] **Step 1: 运行既有生命周期回归测试**

```powershell
& '.\\node_modules\\.bin\\vitest.cmd' run src/realtime/RealtimeVoiceEngine.test.ts src/__tests__/useVoiceSession.test.ts
```

重点确认以下既有用例通过：`stops recognition, both playback paths, capture, queue, and a pending turn exactly once`、`cancels browser speech on a live local switch and ignores its late completion`、`uses local-only without starting browser recognition`，以及 `routes each server event through the engine once and keeps partial ASR transient`。

- [ ] **Step 2: 对照生命周期实现检查合并缓冲清理**

确认 `RealtimeVoiceEngine.ts` 的 `stop()`、`switchMode()`、`cancelCurrentOutput()` 和 `handleTurnCancelled()` 都会清空 `pendingSentences`，并调用队列的 `cancel()`/`reset()`；确认这些路径不会发送新的 WebSocket 事件或重放旧片段。

- [ ] **Step 3: 仅在回归测试失败时做单点修正**

若 Step 1 出现失败，只修改缺失清理点，使用以下固定顺序：

```ts
this.dependencies.sentenceQueue.cancel();
this.pendingSentences = [];
this.dependencies.sentenceQueue.reset();
```

不修改事件名称、服务端协议或回放请求参数。

- [ ] **Step 4: 重跑生命周期测试并提交**

```powershell
& '.\\node_modules\\.bin\\vitest.cmd' run src/realtime/RealtimeVoiceEngine.test.ts src/__tests__/useVoiceSession.test.ts
git add frontend/src/realtime/RealtimeVoiceEngine.ts frontend/src/realtime/RealtimeVoiceEngine.test.ts frontend/src/__tests__/useVoiceSession.test.ts
git commit -m "test: protect realtime speech merge lifecycle"
```

### Task 4: 全量前端验证并重新运行本地服务

**Files:**
- Verify: `frontend/src/`
- Verify: `frontend/dist/`

- [ ] **Step 1: 运行前端完整测试与类型检查**

```powershell
& '.\\node_modules\\.bin\\vitest.cmd' run
& '.\\node_modules\\.bin\\tsc.cmd' --noEmit
```

预期：测试无失败，TypeScript 无错误。

- [ ] **Step 2: 构建前端**

```powershell
& '.\\node_modules\\.bin\\vite.cmd' build
```

预期：构建成功并生成 `frontend/dist`。

- [ ] **Step 3: 重启前端并注入会话令牌**

停止当前前端监听进程后，从 `frontend` 目录启动 Vite，并设置 `VITE_VOXAGENT_TOKEN` 为 `.token_tmp` 中的当前令牌；不停止 Ollama 或后端。

- [ ] **Step 4: 验证运行状态**

确认 `127.0.0.1:5173`、`127.0.0.1:8765`、`127.0.0.1:11434` 均监听，访问 `/healthz` 返回 `{"status":"ok","offline":true}`，并确认 WebSocket 日志出现带令牌的 accepted。

- [ ] **Step 5: 提交最终代码与文档**

```powershell
git diff --check
git status --short
git add frontend/src/voiceSettings.ts frontend/src/__tests__/voiceSettings.test.ts frontend/src/realtime/sentenceQueue.ts frontend/src/realtime/sentenceQueue.test.ts frontend/src/realtime/RealtimeVoiceEngine.ts frontend/src/realtime/RealtimeVoiceEngine.test.ts frontend/src/__tests__/useVoiceSession.test.ts docs/superpowers/specs/2026-09-06-local-realtime-tts-continuity-design.md docs/superpowers/plans/2026-09-06-local-realtime-tts-continuity-implementation.md
git commit -m "feat: improve local realtime speech continuity"
```
