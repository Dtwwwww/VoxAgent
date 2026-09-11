# JR-01 求职基线整备 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把当前开发分支整理成测试全绿、配置一致、可一键验证并能由面试官复现的求职基线。

**Architecture:** 不增加 Agent 新功能；先固定“默认仅本地、在线模式必须显式选择”和短句合并契约，修正实现与测试漂移。统一 PowerShell 验证入口串行执行后端、前端和构建检查，并让 README 只声明真实已实现能力。

**Tech Stack:** React 19、TypeScript 7、Vitest 4、Python 3.12、pytest、Ruff、PowerShell、Git

## Global Constraints

- 从提交 `3e1d3e2` 创建隔离 worktree；不得覆盖当前工作树中的未提交文件。
- 默认语音模式固定为 `local-only`；只有测试或用户明确选择时才进入 `online-preferred`。
- 不把 Session Token 写入 `localStorage`、构建产物、`.env`、Git 或日志。
- 不改变现有 WebSocket 音频协议、数据库 Schema 或模型文件。
- 所有前端 241 个既有测试必须通过，不能删除失败测试来达标。
- 后端 435 个既有测试必须保持通过或原有条件跳过。
- 每个任务独立提交。

---

### Task 1: 固化本地默认语音设置

**Files:**
- Modify: `frontend/src/voiceSettings.ts`
- Modify: `frontend/src/__tests__/voiceSettings.test.ts`
- Modify: `frontend/src/__tests__/useVoiceSession.test.ts`
- Test: `frontend/src/__tests__/voiceSettings.test.ts`
- Test: `frontend/src/__tests__/useVoiceSession.test.ts`

**Interfaces:**
- Consumes: `VOICE_SETTINGS_KEY`, `VoiceSettings`, `loadVoiceSettings()`
- Produces: 测试辅助函数 `seedVoiceSettings(overrides?: Partial<VoiceSettings>): void`
- Invariant: 无论旧设置中保存什么，首次或迁移后的默认模式都是 `local-only`；在线测试必须显式播种在线设置。

- [x] **Step 1: 为默认模式写明确测试**

在 `voiceSettings.test.ts` 增加：

```ts
it("defaults to local-only and does not persist a browser mode implicitly", () => {
  const storage = new MemoryStorage();
  expect(loadVoiceSettings(storage).speechMode).toBe("local-only");
  expect(JSON.parse(storage.getItem(VOICE_SETTINGS_KEY) ?? "null")).toMatchObject({
    speechMode: "local-only",
  });
});

it("normalizes a previously persisted online mode back to local-only", () => {
  const storage = new MemoryStorage();
  storage.setItem(VOICE_SETTINGS_KEY, JSON.stringify({ speechMode: "online-preferred" }));
  expect(loadVoiceSettings(storage).speechMode).toBe("local-only");
});
```

- [x] **Step 2: 运行测试并确认当前契约**

Run: `cd frontend; pnpm exec vitest run src/__tests__/voiceSettings.test.ts`

Expected: 新测试通过；若第二项失败，只修改 `parseSettings()` 使其固定返回 `local-only`，不修改持久化键名。

- [x] **Step 3: 给浏览器语音测试显式播种设置**

在 `useVoiceSession.test.ts` 的测试辅助区加入：

```ts
function seedVoiceSettings(overrides: Partial<VoiceSettings> = {}): void {
  localStorage.setItem(VOICE_SETTINGS_KEY, JSON.stringify({
    voiceKey: null,
    speed: 1,
    speechMode: "local-only",
    microphoneDeviceId: null,
    microphoneLabel: null,
    browserVoiceKey: null,
    onlineSpeechNoticeAccepted: false,
    ...overrides,
  } satisfies VoiceSettings));
}
```

补充 `VoiceSettings` 和 `VOICE_SETTINGS_KEY` 的导入。所有断言 `provider: "browser"`、调用 `callbacks.onFinal()` 或期待 `BrowserSpeechProvider.speak()` 的测试在 `renderHook()` 前调用：

```ts
seedVoiceSettings({
  speechMode: "online-preferred",
  onlineSpeechNoticeAccepted: true,
  browserVoiceKey: browserVoice.key,
});
```

本地回退测试保持 `local-only`，不得全局把默认值改回在线。

- [x] **Step 4: 运行受影响测试**

Run: `cd frontend; pnpm exec vitest run src/__tests__/voiceSettings.test.ts src/__tests__/useVoiceSession.test.ts`

Expected: 所有默认模式、浏览器识别、手动朗读和回退测试通过；不存在 `callbacks is undefined` 或 `provider local/browser` 断言失败。

- [x] **Step 5: 提交**

```powershell
git add frontend/src/voiceSettings.ts frontend/src/__tests__/voiceSettings.test.ts frontend/src/__tests__/useVoiceSession.test.ts
git commit -m "test: align voice sessions with local-first default"
```

### Task 2: 固化短句合并与实时取消契约

**Files:**
- Modify: `frontend/src/realtime/sentenceQueue.ts`
- Modify: `frontend/src/realtime/sentenceQueue.test.ts`
- Modify: `frontend/src/realtime/RealtimeVoiceEngine.ts`
- Modify: `frontend/src/realtime/RealtimeVoiceEngine.test.ts`
- Test: `frontend/src/realtime/sentenceQueue.test.ts`
- Test: `frontend/src/realtime/RealtimeVoiceEngine.test.ts`

**Interfaces:**
- Consumes: `StreamingSentenceQueue.pushSegments()`, `flushSegments()`, `RealtimeVoiceEngine`
- Produces: 小于 8 个 Unicode code point 的相邻短句合并；完成、取消和模式切换不丢失或恢复旧音频。

- [x] **Step 1: 写出短句合并的规范测试**

```ts
it("merges adjacent short sentences into one speakable segment", () => {
  const queue = new StreamingSentenceQueue();
  expect(queue.push("你好！今天想聊什么？")).toEqual(["你好！今天想聊什么？"]);
});

it("flushes one buffered short tail exactly once", () => {
  const queue = new StreamingSentenceQueue();
  expect(queue.push("好的。")).toEqual([]);
  expect(queue.flush()).toEqual(["好的。"]);
  expect(queue.flush()).toEqual([]);
});
```

- [x] **Step 2: 更新 Engine happy-path 预期而非撤销合并行为**

把 `RealtimeVoiceEngine.test.ts` 中同一增量的：

```ts
expect(browserSpeech.spoken).toEqual(["你好！", "今天想聊什么？"]);
```

改为：

```ts
expect(browserSpeech.spoken).toEqual(["你好！今天想聊什么？"]);
expect(browserSpeech.speak).toHaveBeenCalledWith(
  "你好！今天想聊什么？",
  "zh-voice",
  1,
  expect.anything(),
);
```

只有由合并规则导致的失败可以更新预期；取消、关联 ID、重复播放和 fallback 失败必须修实现。

- [x] **Step 3: 为取消后新一轮增加回归测试**

```ts
it("speaks a new turn after the previous synthesis was cancelled", async () => {
  const { browserSpeech, engine } = makeHarness();
  await engine.start(ONLINE_OPTIONS);
  browserSpeech.runs[0].onFinal("旧问题");
  engine.handleServerEvent(browserAsrFinal(SESSION_ID, 1, "旧问题", 1));
  browserSpeech.runs[0].onSpeechStart();
  browserSpeech.runs[0].onFinal("新问题");
  engine.handleServerEvent(browserAsrFinal(SESSION_ID, 2, "新问题", 2));
  engine.handleServerEvent({
    type: "assistant.delta",
    session_id: SESSION_ID,
    turn_id: 2,
    delta: "新回答。",
  });
  await vi.waitFor(() => expect(browserSpeech.spoken).toEqual(["新回答。"]));
});
```

- [x] **Step 4: 最小修复所有权状态**

在 `RealtimeVoiceEngine` 中把取消所有权与当前生成所有权分开保存。实现必须遵守以下形状：

```ts
private activeGeneration = 0;
private cancelledThroughGeneration = 0;

private ownsGeneration(generation: number): boolean {
  return generation === this.activeGeneration
    && generation > this.cancelledThroughGeneration;
}
```

开始新用户转写时递增 `activeGeneration`；取消时只标记当时 generation，不得清除后来 generation 的浏览器回声或朗读队列。

- [x] **Step 5: 运行实时语音测试**

Run: `cd frontend; pnpm exec vitest run src/realtime/sentenceQueue.test.ts src/realtime/RealtimeVoiceEngine.test.ts`

Expected: 两个文件全部通过；不存在超时等待 `browserSpeech.spoken` 的测试。

- [x] **Step 6: 提交**

```powershell
git add frontend/src/realtime/sentenceQueue.ts frontend/src/realtime/sentenceQueue.test.ts frontend/src/realtime/RealtimeVoiceEngine.ts frontend/src/realtime/RealtimeVoiceEngine.test.ts
git commit -m "fix: restore realtime voice regression coverage"
```

### Task 3: 增加统一验证入口

**Files:**
- Create: `scripts/verify.ps1`
- Create: `backend/tests/scripts/test_verify_script.py`
- Modify: `README.md`
- Test: `backend/tests/scripts/test_verify_script.py`

**Interfaces:**
- Produces: `scripts/verify.ps1 -Scope All|Backend|Frontend`
- Exit contract: 任一子命令非零则最终退出非零，同时仍运行另一个独立测试组。

- [x] **Step 1: 写验证脚本结构测试**

```python
from pathlib import Path


def test_verify_script_has_all_required_gates() -> None:
    script = Path(__file__).parents[3] / "scripts" / "verify.ps1"
    text = script.read_text(encoding="utf-8")
    for command in ("pytest", "ruff", "vitest", "tsc.cmd", "vite.cmd"):
        assert command in text
    assert "exit 1" in text
```

- [x] **Step 2: 运行测试确认失败**

Run: `cd backend; uv run --extra dev pytest tests/scripts/test_verify_script.py -v`

Expected: FAIL because `scripts/verify.ps1` does not exist.

- [x] **Step 3: 实现脚本**

```powershell
param(
    [ValidateSet('All', 'Backend', 'Frontend')]
    [string]$Scope = 'All'
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Failures = [System.Collections.Generic.List[string]]::new()

function Invoke-Gate {
    param([string]$Name, [string]$WorkingDirectory, [scriptblock]$Command)
    Push-Location $WorkingDirectory
    try {
        & $Command
        if ($LASTEXITCODE -ne 0) { $Failures.Add($Name) }
    } catch {
        $Failures.Add($Name)
        Write-Warning "$Name failed to start: $($_.Exception.GetType().Name)"
    } finally {
        Pop-Location
    }
}

if ($Scope -in @('All', 'Backend')) {
    Invoke-Gate 'backend-pytest' (Join-Path $RepoRoot 'backend') {
        uv run --extra dev pytest -q
    }
    Invoke-Gate 'backend-ruff' (Join-Path $RepoRoot 'backend') {
        uv run --extra dev ruff check src tests
    }
}

if ($Scope -in @('All', 'Frontend')) {
    Invoke-Gate 'frontend-vitest' (Join-Path $RepoRoot 'frontend') {
        pnpm exec vitest run
    }
    Invoke-Gate 'frontend-typecheck' (Join-Path $RepoRoot 'frontend') {
        pnpm exec tsc --noEmit
    }
    Invoke-Gate 'frontend-build' (Join-Path $RepoRoot 'frontend') {
        pnpm exec vite build
    }
}

if ($Failures.Count -gt 0) {
    Write-Error ('Verification failed: ' + ($Failures -join ', '))
    exit 1
}
Write-Host 'Verification passed.'
```

- [x] **Step 4: 运行结构测试和完整验证**

Run: `cd backend; uv run --extra dev pytest tests/scripts/test_verify_script.py -v`

Expected: PASS.

Run: `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1 -Scope All`

Expected: `Verification passed.` and exit 0.

- [x] **Step 5: 提交**

```powershell
git add scripts/verify.ps1 backend/tests/scripts/test_verify_script.py README.md
git commit -m "test: add one-command project verification"
```

### Task 4: 清理仓库边界和求职说明

**Files:**
- Modify: `.gitignore`
- Modify: `README.md`
- Create: `docs/portfolio/current-capabilities.md`
- Create: `docs/portfolio/demo-script.md`
- Test: `backend/tests/scripts/test_repository_hygiene.py`

**Interfaces:**
- Produces: 清晰区分“已实现”“下一阶段”“已知限制”的公开入口。
- Invariant: 本地令牌、模型、录音、数据库、临时评测和依赖目录不能被 Git 跟踪。

- [x] **Step 1: 写仓库卫生测试**

```python
from pathlib import Path


def test_gitignore_covers_private_runtime_artifacts() -> None:
    text = (Path(__file__).parents[3] / ".gitignore").read_text(encoding="utf-8")
    for pattern in (
        ".token_tmp",
        "node_modules/",
        ".pytest-*/",
        "benchmarks/voice-review-*/",
        "*.db-wal",
        "*.db-shm",
    ):
        assert pattern in text
```

- [x] **Step 2: 增加精确忽略项**

```gitignore
.token_tmp
node_modules/
backend/.pytest-*/
benchmarks/voice-review-*/
*.db-wal
*.db-shm
frontend/dist/
qa/reports/local-*.json
```

不得增加会忽略全部 `benchmarks/*.json` 或全部 `qa/reports/*.json` 的宽泛规则。

- [x] **Step 3: 重写 README 首屏**

README 首屏必须按以下顺序出现：

```markdown
# VoxAgent（声灵）

本地优先的 Windows 流式语音 AI 应用。当前已完成实时语音闭环、长期记忆和本地知识检索；安全 Agent 工具、MCP 和 Hybrid RAG 按求职增强路线实施。

## 当前可运行能力
## 架构
## 目标机器实测
## 五分钟启动
## 一键验证
## 隐私和安全边界
## 已知限制
## 路线图
```

“当前可运行能力”只能引用现有代码和已提交报告，不能提前宣称 Tool Calling、MCP 或 Hybrid RAG 已完成。

- [x] **Step 4: 编写三分钟演示脚本**

`docs/portfolio/demo-script.md` 固定演示：

1. 展示本地回环地址和离线模型。
2. 完成一轮文字问答。
3. 导入公开示例文档并展示引用。
4. 保存、编辑、删除一条非敏感记忆。
5. 开始本地语音、打断一次、展示真实限制。

每一步写明操作、预期界面、需要讲解的工程取舍和失败时停止条件。

- [x] **Step 5: 验证并提交**

Run: `cd backend; uv run --extra dev pytest tests/scripts/test_repository_hygiene.py -v`

Expected: PASS.

Run: `git status --short --ignored`

Expected: 依赖、临时测试、令牌和私有评测显示为 ignored；没有新生成的敏感文件进入暂存区。

```powershell
git add .gitignore README.md docs/portfolio/current-capabilities.md docs/portfolio/demo-script.md backend/tests/scripts/test_repository_hygiene.py
git commit -m "docs: establish reproducible portfolio baseline"
```

### Task 5: 完成 JR-01 验收

**Files:**
- Create: `qa/reports/jr-01-baseline.json`
- Modify: `docs/superpowers/plans/2026-09-09-jr-01-portfolio-baseline.md`

**Interfaces:**
- Produces: 绑定 Git 提交的基线报告。

- [x] **Step 1: 运行统一验证**

Run: `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1 -Scope All`

Expected: 所有 gate 通过，exit 0。

- [x] **Step 2: 记录机器可读结果**

在仓库根目录执行以下 PowerShell，让提交值来自 Git 而不是手工填写：

```powershell
$Report = [ordered]@{
    schema_version = 1
    plan = 'JR-01'
    git_commit = (git rev-parse HEAD).Trim()
    backend_existing_test_floor = 435
    frontend_existing_test_floor = 241
    verification_command = 'scripts/verify.ps1 -Scope All'
    verification_exit_code = 0
    backend = 'passed'
    frontend = 'passed'
    ruff = 'passed'
    typescript = 'passed'
    vite_build = 'passed'
    manual_claims = 'not_run'
}
$Report | ConvertTo-Json | Set-Content -Encoding utf8 qa/reports/jr-01-baseline.json
```

Run: `Get-Content qa/reports/jr-01-baseline.json -Raw | ConvertFrom-Json | Select-Object git_commit`

Expected: `git_commit` 是 40 位当前提交 SHA，文件中没有尖括号占位符。

- [x] **Step 3: 检查工作树**

Run: `git status --short`

Expected: 只显示本任务的报告和计划勾选修改；没有模型、令牌、数据库、录音、`node_modules` 或构建目录。

- [x] **Step 4: 提交验收证据**

```powershell
git add qa/reports/jr-01-baseline.json docs/superpowers/plans/2026-09-09-jr-01-portfolio-baseline.md
git commit -m "test: approve JR-01 portfolio baseline"
```

## JR-01 Completion Gate

- 前端原有 241 个测试和本计划新增测试全部通过。
- 后端原有 435 个测试和本计划新增测试全部通过或保持原有条件跳过。
- Ruff、TypeScript 和 Vite 构建通过。
- 默认模式、测试和文档一致为 `local-only`。
- `scripts/verify.ps1 -Scope All` 单命令退出 0。
- README 不声明尚未实现的 Agent、MCP 或 Hybrid RAG。
- 私有运行数据和依赖目录全部处于忽略状态。
- `qa/reports/jr-01-baseline.json` 绑定真实 Git 提交。

## Execution Record（2026-09-09）

- 验证提交：`1f6d7659773533a2951f617298beeb35381a8f25`。
- 干净提交快照执行 `scripts/verify.ps1 -Scope All`：后端收集 437 项（436 通过、1 项原有条件跳过），Ruff 通过；前端 240 项通过，TypeScript 与 Vite 构建通过。
- 当前工作区还包含任务开始前已有、未暂存的 `frontend/src/main.tsx`、`frontend/src/token.ts` 和 `frontend/src/__tests__/token.test.ts`；工作区前端口径为 242 项通过。这些文件未纳入 JR-01 提交，也未冒充为绑定提交的证据。
- `loadVoiceSettings()` 会把持久化的在线模式迁回 `local-only`，因此不能按原示例在 `localStorage` 预置在线模式。在线测试改为在 Hook 创建后显式接受说明并调用 `setSpeechMode("online-preferred")`，安全契约不变。
- 短句合并实现和取消所有权隔离在执行前已存在；红灯来自 Engine 旧用例仍假定短句会立即发声，以及一个用例未等待自动朗读启动。对齐 `assistant.done` 冲刷契约和异步前置条件后，取消、旧事件隔离、fallback 与请求 ID 回归全部通过，无证据支持额外修改生产所有权状态。
- 验证脚本优先使用仓库本地 `.venv` 与 `node_modules`，缺失时回退 `uv` / `pnpm`；pytest 临时目录位于系统临时区并禁用工作区缓存写入。
- 真机物理麦克风、扬声器听感和批量插话未在 JR-01 重跑，报告中的 `manual_claims` 保持 `not_run`。
