# VoxAgent（声灵）

本地优先的 Windows 流式语音 AI 应用。当前已完成实时语音、长期记忆、本地知识检索、安全单 Agent 工具 MVP 和本地 stdio MCP 互操作；Hybrid RAG 按求职增强路线实施。

面向初级大模型应用 / Agent 工程师作品集：不仅展示对话界面，也保留模型选型、资源测量、严格协议、失败回退和自动化验证证据。

## 当前可运行能力

- 文字与语音共用同一会话；文字输入始终静默回复，实时语音支持流式字幕、短句朗读、插话取消和旧回调隔离。
- 语音默认 `local-only`：麦克风 PCM、SenseVoice/Paraformer ASR、Ollama LLM 与 Kokoro TTS 均走本机回环地址；浏览器在线语音必须由用户显式选择。
- FastAPI HTTP / WebSocket 服务使用严格事件协议和启动期 Session Token，前后端都校验消息关联 ID。
- SQLite 保存会话、单一人格和需确认的长期记忆；敏感记忆由后端策略拦截，并保留来源轮次。
- 可导入 TXT、Markdown、文本 PDF 与 DOCX，使用本地 BGE embedding 做向量检索，并在回答下展示命中的记忆和知识片段。
- Qwen3 原生 Tool Calling 接入有界 LangGraph；六个冻结注册工具按 L0/L1/L2 分级，写操作和应用启动必须单次确认，并保留脱敏审计。
- 工具运行时可选择 `native` 或 `mcp`：四个知识/提醒工具可通过固定本地 stdio MCP 子进程执行，文件搜索和应用启动仍留在宿主；一次性 capability 绑定请求、工具和参数并防重放。
- 支持本地数据导出、完整清空、每日备份、模型身份校验和 CPU / RAM / VRAM / 延迟基准。

实现证据与边界见 [当前能力清单](docs/portfolio/current-capabilities.md)，三分钟演示流程见 [演示脚本](docs/portfolio/demo-script.md)。

## 架构

```mermaid
flowchart LR
    UI[React 19 / TypeScript / Vite] -->|HTTP + WebSocket<br/>Session Token| API[FastAPI 本地服务]
    API --> ORCH[ConversationOrchestrator<br/>流式会话与确认恢复]
    ORCH --> AGENT[LangGraph<br/>8 节点 / 3 工具调用上限]
    AGENT --> LLM[Ollama / Qwen3 4B Tool Calling]
    AGENT --> REGISTRY[冻结 Tool Registry<br/>L0 / L1 / L2 + 审计]
    REGISTRY --> NATIVE[宿主原生工具<br/>文件搜索 / 应用启动]
    REGISTRY --> MCPCLIENT[MCP Client<br/>固定本地 stdio]
    MCPCLIENT --> MCPSERVER[MCP Server<br/>知识 / 提醒]
    ORCH --> DATA[SQLite<br/>人格 / 记忆 / 文档分段]
    ORCH --> RAG[BGE 本地向量检索]
    UI --> BROWSER[可选浏览器语音]
    API --> SPEECH[本地 VAD / ASR / Kokoro TTS]
```

运行模型、缓存、数据库、导出与日志默认位于 `D:\VoxAgentData`，不进入 Git。仓库内保存代码、测试、模型清单和脱敏后的基准摘要。

## 目标机器实测

提交的 [目标机器基线](benchmarks/target-machine-baseline.json) 来自 i5-9300H（4 核 8 线程）、15.88 GB RAM、GTX 1660 Ti 6 GB 的 Windows 电脑。

| 项目 | 实测结果 | 说明 |
| --- | ---: | --- |
| 默认 LLM | `qwen3:4b-instruct-2507-q4_K_M` | 本机 Ollama，8K 上下文 |
| LLM p95 首 Token | 0.306 s | 3 次固定中文提示，模型身份有 SHA-256 证明 |
| LLM 峰值显存 | 3,783 MiB | 固定提示资源探针 |
| 本地语音：停句到转写 | 0.984 s | 一次真实模型合成回环 |
| 转写到首 Token | 0.229 s | 同一次真实本地闭环 |
| 首 Token 到音频 | 3.919 s | Kokoro CPU 路径，仍是已知体验瓶颈 |

`qwen3.5:4b` 虽通过质量检查，但 30 分钟稳定性测试在约 431 秒因内存压力主动终止，因此没有被选为默认模型。语音自动回环已通过；物理麦克风、扬声器听感与 20 次插话仍需人工验收，不能由单测替代。详见 [语音闭环报告](benchmarks/voice-loop-acceptance.json)。

## 启动说明（Windows）

启动链路固定为 `Ollama → 后端 → 前端`。三个服务分别监听 `127.0.0.1:11434`、`127.0.0.1:8765` 和 `127.0.0.1:5173`；只启动前端页面会一直显示“本地服务未启动”。

### 0. 首次运行准备

需要 Python 3.12、[uv](https://docs.astral.sh/uv/)、Node.js、pnpm 以及 Ollama。本项目锁定 Python `<3.13`；目标电脑当前使用 Node.js 24 和 pnpm 11。

新克隆仓库后，在仓库根目录执行：

```powershell
uv sync --project .\backend --extra dev --extra speech --frozen
pnpm --dir .\frontend install --frozen-lockfile

& .\scripts\download_embedding_model.ps1 -DataRoot 'D:\VoxAgentData'
& .\scripts\download_speech_models.ps1 -DataRoot 'D:\VoxAgentData'
```

目标电脑使用的 Ollama 可执行文件位于 `D:\VoxAgentData\runtime\ollama\ollama.exe`，默认模型是 `qwen3:4b-instruct-2507-q4_K_M`。使用系统安装版 Ollama 时，可将后文的完整路径替换为 `ollama`。模型和语音资产的详细说明见 [Plan 1](docs/plan-01/README.md) 和 [Plan 2](docs/plan-02/README.md)。

### 1. 生成本次会话 Token

在 PowerShell 生成 Token。下面的写法兼容没有静态 `RandomNumberGenerator.GetBytes()` 方法的旧版 Windows PowerShell：

```powershell
$tokenBytes = New-Object byte[] 32
$tokenRng = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $tokenRng.GetBytes($tokenBytes) } finally { $tokenRng.Dispose() }
$sessionToken = [Convert]::ToBase64String($tokenBytes).TrimEnd('=').Replace('+','-').Replace('/','_')
$sessionToken
```

保留输出值，后端命令和浏览器 URL 必须使用完全相同的 Token。不要直接使用示例文字“这里粘贴 Token”。

### 2. 启动 Ollama

在第一个 PowerShell 窗口启动项目自带的便携版 Ollama；它不在系统 `PATH` 中：

```powershell
$env:OLLAMA_MODELS = 'D:\VoxAgentData\models\ollama'
$env:OLLAMA_NO_CLOUD = '1'
$env:OLLAMA_HOST = '127.0.0.1:11434'
& 'D:\VoxAgentData\runtime\ollama\ollama.exe' serve
```

看到 `Listening on 127.0.0.1:11434` 后保持窗口运行。如果端口已经监听，说明 Ollama 已在运行，无需重复启动。可在另一个窗口检查服务和模型：

```powershell
$ollamaState = Invoke-RestMethod 'http://127.0.0.1:11434/api/tags'
$ollamaState.models.name
```

输出中应包含 `qwen3:4b-instruct-2507-q4_K_M`。如果缺少该模型，在 Ollama 服务保持运行时执行：

```powershell
$env:OLLAMA_MODELS = 'D:\VoxAgentData\models\ollama'
& 'D:\VoxAgentData\runtime\ollama\ollama.exe' pull 'qwen3:4b-instruct-2507-q4_K_M'
```

### 3. 启动后端

在仓库根目录打开第二个 PowerShell 窗口：

```powershell
$sessionToken = '粘贴刚才生成的 Token'
$env:VOXAGENT_DATA_ROOT = 'D:\VoxAgentData'
Set-Location .\backend
uv run --frozen voxagent serve --session-token $sessionToken --port 8765
```

保持后端窗口运行。看到 Uvicorn 启动信息后，在第三个窗口验证：

```powershell
Invoke-RestMethod 'http://127.0.0.1:8765/healthz'
```

应返回 `status: ok`。如果后端在启动阶段报告 embedding 或 speech 模型缺失，回到“首次运行准备”重新执行对应下载脚本。

默认使用宿主原生工具。要演示 JR-03 的本地 MCP 工具链，将后端最后一行改为：

```powershell
uv run --frozen voxagent serve --session-token $sessionToken --port 8765 --tool-provider mcp
```

MCP 子进程由后端通过固定 stdio 命令管理，不需要另外启动端口。启动或工具发现失败时后端会明确报错，不会静默降级。

### 4. 启动前端

在仓库根目录打开第三个 PowerShell 窗口：

```powershell
Set-Location .\frontend
pnpm exec vite --host 127.0.0.1 --port 5173
```

### 5. 打开页面

浏览器访问：

```text
http://127.0.0.1:5173/?token=粘贴同一个Token
```

正常状态应满足：

- Ollama 的 `/api/tags` 可访问，并能看到默认 Qwen3 模型。
- 后端 `/healthz` 返回 `status: ok`。
- 页面不再显示“本地服务未连接”，文字消息可以得到本地模型回复。

### 常见启动问题

| 现象 | 优先检查 |
| --- | --- |
| 页面显示“本地服务未启动” | 确认后端窗口仍在运行，并检查 `Invoke-RestMethod 'http://127.0.0.1:8765/healthz'`。 |
| 页面能打开，但一直“本地服务未连接” | URL 中不能保留示例 Token；重新生成 Token，并让后端参数与 `?token=` 后的值完全一致。刷新旧页面不会自动更新 Token。 |
| 显示“本地 Agent 执行失败” | 检查 `http://127.0.0.1:11434/api/tags`，确认 Ollama 在线且默认模型已经安装；具体原因同时会打印在后端窗口。 |
| `RandomNumberGenerator.GetBytes` 方法不存在 | 使用本文的 `Create()`、实例 `GetBytes()` 和 `Dispose()` 写法，不要调用静态 `GetBytes(32)`。 |
| 后端提示模型文件缺失 | 重新运行 embedding 或 speech 下载脚本；模型默认应位于 `D:\VoxAgentData\models`。 |
| 端口 5173、8765 或 11434 被占用 | 先关闭之前启动的对应终端或服务，再按 Ollama、后端、前端的顺序启动。 |

停止项目时，在三个服务窗口分别按 `Ctrl+C`；这不会删除模型、数据库或会话记录。

## 一键验证

在仓库根目录执行：

```powershell
& .\scripts\verify.ps1 -Scope All
```

脚本依次运行后端 pytest、Ruff、前端 Vitest、TypeScript 和 Vite 构建；任一 gate 失败时最终退出非零，同时仍会执行其他独立检查。开发时可改用 `-Scope Backend` 或 `-Scope Frontend`。

Agent MVP 的 12 条确定性路由和 Schema 冒烟评测：

```powershell
Set-Location .\backend
uv run --extra dev voxagent evaluate-agent --dataset ..\benchmarks\agent-mvp-dataset.json --mode fake
```

真实 Ollama 试跑可将 `--mode` 改为 `local` 并用 `--model` 指定本机 Qwen3；它只评估首轮工具选择，不自动批准或执行工具。

MCP 的无模型确定性演示：

```powershell
& .\scripts\demo-mcp.ps1
```

脚本使用临时数据目录启动真实 stdio Server/Client，发现四个工具，执行 L0 查询，证明未授权 L2 被拒绝，再批准一次写入并证明 capability 无法重放。成功时输出 `MCP demo passed`。

## 隐私和安全边界

- 后端、Ollama 与前端开发服务只监听 `127.0.0.1`；API 与 WebSocket 共用一次性 Session Token。
- Token 不写入 `localStorage`、数据库、构建产物、日志或 Git。请勿把带 Token 的完整 URL 放进截图和演示录像。
- 默认仅本地语音不调用浏览器在线识别或非本机语音服务，也不持久化原始麦克风音频。
- 文档内容与记忆作为不可信参考数据注入，不允许覆盖固定的隐私、安全和工具边界。
- 模型只能调用启动时冻结的六个工具；没有 Shell、PowerShell、Python、删除、移动或安装入口。L1/L2 票据绑定 session、turn 和参数哈希，120 秒过期且只能消费一次。
- 清空全部本地数据需要准确输入确认短语；知识文档删除只删本地索引，不删除用户原始文件。

## 已知限制

- JR-02 当前是单 Agent MVP，不是多 Agent 平台；固定评测为 12 条 fake 冒烟集，60 条正式 Agent 集与真实模型稳定性验收仍待 JR-06。
- 工具确认后的恢复回答暂不自动朗读；授权目录目前依赖 SQLite 配置，尚无前端目录管理器。
- MCP 当前只支持本地 stdio；知识和提醒四个工具可走 MCP，文件搜索和应用启动为缩小权限边界仍由宿主原生执行，不开放 HTTP/SSE 或第三方任意 Server 配置。
- 当前知识检索是本地向量检索，不是 BM25 + Vector + RRF + Reranker 的 Hybrid RAG，也没有固定金标 RAG 评测集。
- 文本型 PDF 可解析，扫描 PDF 不做 OCR；单文件上限 20 MB。
- Kokoro 本地 CPU 合成首段延迟约 3.9 秒；浏览器在线语音的可用性和隐私取决于浏览器与操作系统平台。
- 尚未完成 Windows CI、结构化日志 / Metrics、Docker 无模型演示包和完整真机人工语音验收。

## 路线图

求职增强按以下顺序推进，未完成项不会提前写入“当前能力”：

1. **JR-01 基线整备：** 全绿测试、一键验证、仓库卫生和作品集说明。
2. **JR-02 安全 Agent 工具 MVP（已完成）：** 有界状态图、原生 Tool Calling、六个工具、权限确认、防重放、前端确认卡和审计；正式评测扩充留到 JR-06。
3. **JR-03 MCP（已完成）：** 本地 stdio Server / Client、统一 provider、一次性 capability 和安全边界演示。
4. **JR-04 Hybrid RAG：** FTS5 + Vector + RRF + ONNX Reranker 与金标评测。
5. **JR-05 可观测与交付：** 脱敏日志、Metrics、CI、Docker Demo 和运维文档。
6. **JR-06 硬件验收：** 真实插话、30 分钟稳定性和最终求职发布证据。

完整顺序、验收阈值和岗位映射见 [初级 Agent 求职增强路线图](docs/superpowers/plans/2026-09-09-junior-agent-job-readiness-roadmap.md)；杭州岗位差距分析见 [岗位匹配报告](docs/portfolio/hangzhou-junior-agent-role-gap-analysis.md)。
