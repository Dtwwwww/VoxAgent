# JR-05 可观测性与交付 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让声灵具备面试可验证的日志、指标、CI、无模型 Docker Demo 和项目说明，并确保这些工程化能力不泄露用户内容或令牌。

**Architecture:** API、会话、Agent、Tool、RAG 和语音阶段统一使用 ContextVar 关联 ID，输出脱敏 JSON Lines；Prometheus 指标只使用有限枚举标签。GitHub Actions 在 Windows 上运行完整无模型测试，Docker 在 Linux 上运行明确标注的 deterministic demo runtime，不加载真实语音、Embedding、Reranker 或 Ollama。

**Tech Stack:** Python logging、ContextVar、prometheus-client 0.26、FastAPI、pytest-cov、GitHub Actions、Docker Compose、React/Vite

## Global Constraints

- 日志禁止记录 Session Token、MCP capability、用户原文、Prompt、工具完整参数、文档内容、绝对路径和音频字节。
- request/session/turn/call ID 可记录，但 session ID 在日志中只保留 SHA-256 前 12 位。
- Metrics label 只允许代码内固定枚举；禁止 user ID、session ID、turn ID、request ID、文档名、错误消息或路径标签。
- `/metrics` 使用与 REST 相同的 Bearer Token；`/healthz` 保持无鉴权且只返回低敏状态。
- CI 不下载模型、不访问 Ollama、不需要 GPU、麦克风或音频设备。
- Docker Demo 必须在页面上永久显示“演示模式：未连接真实模型”，且只能绑定 `127.0.0.1`。
- Docker 不挂载真实 `D:\VoxAgentData`，不读取宿主令牌，不承诺完整语音能力。
- 每个任务独立提交。

---

### Task 1: 建立关联上下文和脱敏 JSON 日志

**Files:**
- Create: `backend/src/voxagent/observability/__init__.py`
- Create: `backend/src/voxagent/observability/context.py`
- Create: `backend/src/voxagent/observability/logging.py`
- Create: `backend/src/voxagent/observability/redaction.py`
- Modify: `backend/src/voxagent/cli.py`
- Test: `backend/tests/observability/test_context.py`
- Test: `backend/tests/observability/test_logging.py`
- Test: `backend/tests/observability/test_redaction.py`

**Interfaces:**
- `bind_observation_context(request_id, session_id, turn_id, tool_call_id)` context manager
- `configure_json_logging(log_directory: Path, level: str) -> None`
- `redact_fields(event: Mapping[str, object]) -> dict[str, object]`

- [ ] **Step 1: 写敏感信息负例**

使用固定 canary 值作为 token、capability、用户文本、Windows 路径和工具参数，写入不同嵌套层级后捕获日志；断言序列化文本不包含任何 canary，且包含 `redacted_fields` 计数。

- [ ] **Step 2: 定义结构化事件合同**

每行至少包含 `timestamp_utc`、`level`、`event`、`component`、`request_id` 和 `duration_ms`；可选 `session_hash`、`turn_id`、`tool_call_id`、`status`、`error_code`。时间使用 UTC RFC 3339 毫秒格式，duration 为非负整数。

- [ ] **Step 3: 实现 allow-list redaction**

不是递归“寻找疑似 secret”，而是每种事件只从允许字段构造新 dict。异常只记录异常类名和稳定 `error_code`，不调用 `traceback.format_exc()` 写生产日志。开发测试失败仍由 pytest 显示堆栈。

- [ ] **Step 4: 配置轮转文件和 stderr**

文件写到 `AppPaths.logs / "voxagent.jsonl"`，每个 10 MiB、保留 5 个；stderr 使用相同 JSON formatter。`serve --log-level` 只接受 `DEBUG|INFO|WARNING|ERROR`，默认 INFO。DEBUG 也不能放宽敏感字段。

- [ ] **Step 5: 运行并提交**

Run: `cd backend; uv run pytest tests/observability/test_context.py tests/observability/test_logging.py tests/observability/test_redaction.py -v`

Expected: PASS；canary 扫描 0 命中。

```powershell
git add backend/src/voxagent/observability backend/src/voxagent/cli.py backend/tests/observability
git commit -m "feat: add privacy-safe structured logging"
```

### Task 2: 埋点 Agent、Tool、RAG 与语音阶段

**Files:**
- Modify: `backend/src/voxagent/api/app.py`
- Modify: `backend/src/voxagent/conversation/orchestrator.py`
- Modify: `backend/src/voxagent/agent/service.py`
- Modify: `backend/src/voxagent/tools/registry.py`
- Modify: `backend/src/voxagent/rag/retriever.py`
- Test: `backend/tests/observability/test_instrumentation.py`

**Interfaces:**
- Events: `session.started`, `turn.started`, `agent.routed`, `tool.finished`, `rag.finished`, `asr.finished`, `llm.first_token`, `tts.first_audio`, `turn.finished`, `turn.cancelled`

- [ ] **Step 1: 写跨层关联测试**

使用 fake 完成一轮“文本→Agent→知识工具→回答”，捕获 JSONL，断言所有事件有同一 request ID、同一 session hash 和 turn ID；工具事件另有 call ID。再运行取消和失败路径，断言 terminal 事件恰好一个。

- [ ] **Step 2: 在边界计时**

统一使用 `time.perf_counter_ns()`，只在完成时转换毫秒。TTFT 从 LLM 请求开始到第一个非空文本 delta；TTS first audio 从第一句提交到第一个音频 chunk；取消 latency 从 cancel event 到音频队列清空确认。

- [ ] **Step 3: 限定事件字段**

RAG 只记录 mode、candidate count、result count、reranker used 和 duration；Tool 只记录已注册 tool name、permission、status、error code 和 duration；语音只记录 stage、provider enum 和 duration。不得记录文本长度以外的用户内容特征。

- [ ] **Step 4: 运行并提交**

Run: `cd backend; uv run pytest tests/observability/test_instrumentation.py -v`

Expected: PASS；成功、失败、取消每轮只有一个 terminal event。

```powershell
git add backend/src/voxagent/api/app.py backend/src/voxagent/conversation/orchestrator.py backend/src/voxagent/agent/service.py backend/src/voxagent/tools/registry.py backend/src/voxagent/rag/retriever.py backend/tests/observability/test_instrumentation.py
git commit -m "feat: correlate agent and voice runtime events"
```

### Task 3: 增加低基数 Prometheus Metrics

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock`
- Create: `backend/src/voxagent/observability/metrics.py`
- Modify: `backend/src/voxagent/api/app.py`
- Test: `backend/tests/observability/test_metrics.py`
- Modify: `backend/tests/api/test_app.py`

**Interfaces:**
- Authenticated endpoint: `GET /metrics`
- Counters: `voxagent_turns_total`, `voxagent_tool_calls_total`, `voxagent_rag_queries_total`, `voxagent_errors_total`
- Histograms: `voxagent_stage_duration_seconds`, `voxagent_turn_duration_seconds`, `voxagent_cancel_latency_seconds`
- Gauges: `voxagent_active_sessions`, `voxagent_pending_tool_confirmations`, `voxagent_queue_depth`

- [ ] **Step 1: 固定依赖**

在生产 dependencies 增加：

```toml
"prometheus-client>=0.26,<0.27",
```

Run: `cd backend; uv lock`

Expected: lock 文件只增加 prometheus-client 及其必需解析变化。

- [ ] **Step 2: 写鉴权和 label 测试**

无 Authorization、错误 token 返回 401；正确 Bearer 返回 Prometheus text format。遍历所有 metric sample label，断言 label key 只属于 `stage`、`status`、`tool`、`permission`、`mode`、`error_code`、`queue`、`provider`。

- [ ] **Step 3: 建立独立 registry**

应用使用自己的 `CollectorRegistry`，避免测试间全局重复注册。Histogram bucket 固定覆盖 10ms、25ms、50ms、100ms、250ms、500ms、1s、2.5s、5s、10s、30s；tool label 先由冻结注册表验证。

- [ ] **Step 4: 接入生命周期**

Gauge 必须用 `try/finally` 成对增减；WebSocket 异常断开、Agent cancel、MCP timeout 和 shutdown 后都回到 0。队列只记录 microphone、socket_writer、tts 三个固定名称。

- [ ] **Step 5: 运行并提交**

Run: `cd backend; uv run pytest tests/observability/test_metrics.py tests/api/test_app.py -v`

Expected: PASS；连续创建两个 app 不产生 duplicate timeseries。

```powershell
git add backend/pyproject.toml backend/uv.lock backend/src/voxagent/observability/metrics.py backend/src/voxagent/api/app.py backend/tests/observability/test_metrics.py backend/tests/api/test_app.py
git commit -m "feat: expose bounded local runtime metrics"
```

### Task 4: 加入覆盖率和一键证据报告

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock`
- Modify: `scripts/verify.ps1`
- Create: `scripts/collect-verification-report.ps1`
- Create: `qa/reports/verification-report.schema.json`
- Test: `backend/tests/scripts/test_verification_report.py`

**Interfaces:**
- Verification: `scripts/verify.ps1 -Scope All -Coverage`
- Report: `scripts/collect-verification-report.ps1 -Output qa/reports/local-verification.json`

- [ ] **Step 1: 加开发依赖和覆盖率 gate**

在 dev dependencies 增加 `pytest-cov>=6,<8`。`-Coverage` 时额外运行：

```powershell
uv run pytest tests/tools tests/mcp tests/security `
  --cov=voxagent.tools --cov=voxagent.mcp --cov-branch `
  --cov-report=term-missing --cov-fail-under=85
```

该 gate 只覆盖安全关键模块；普通全项目测试仍完整运行。

- [ ] **Step 2: 写报告 Schema 测试**

报告字段固定为 commit、dirty、started_at_utc、finished_at_utc、hardware summary、各 gate 的 command/exit code/duration、test counts 和 artifact SHA-256。命令输出只保留最后 4 KiB 且先走 canary redaction。

- [ ] **Step 3: 实现证据收集脚本**

脚本调用 `verify.ps1 -Scope All -Coverage`，即使某个 gate 失败也写合法 JSON，最终返回相同非零状态。dirty 列表只记相对路径和 Git 状态，不读文件内容。

- [ ] **Step 4: 运行并提交**

Run: `powershell -ExecutionPolicy Bypass -File scripts/collect-verification-report.ps1 -Output qa/reports/local-verification.json`

Expected: JSON 通过 Schema；若基线尚未全绿则 exit 非零并准确列出 gate，不伪报成功。

```powershell
git add backend/pyproject.toml backend/uv.lock scripts/verify.ps1 scripts/collect-verification-report.ps1 qa/reports/verification-report.schema.json backend/tests/scripts/test_verification_report.py
git commit -m "test: collect reproducible verification evidence"
```

### Task 5: 建立 Windows GitHub Actions CI

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `.github/dependabot.yml`
- Test: `backend/tests/scripts/test_ci_workflow.py`

**Interfaces:**
- Triggers: pull request、push to `main`
- Jobs: `backend`, `frontend`, `security-coverage`

- [ ] **Step 1: 写静态工作流测试**

测试 workflow 使用 `windows-latest`、Python 3.12、Node 24、pnpm 11.19.0、`uv sync --extra dev --frozen`、完整 pytest/Ruff、Vitest/typecheck/build 和 85% 安全覆盖率。permissions 只能是 `contents: read`。

- [ ] **Step 2: 实现三个独立 job**

Actions 使用 `actions/checkout@v7`、`actions/setup-python@v7`、`astral-sh/setup-uv@v9`、`actions/setup-node@v6`、`pnpm/action-setup@v4`。backend 缓存键包含 `backend/uv.lock`，frontend 缓存键包含 `frontend/pnpm-lock.yaml`。所有 install 使用 frozen lock；模型测试 marker 显式排除。

- [ ] **Step 3: 增加 Dependabot**

每周检查 `pip`、`npm` 和 `github-actions`，每类最多 5 个 open PR；不自动合并、不请求 write permission。

- [ ] **Step 4: 本地解析并提交**

Run: `cd backend; uv run pytest tests/scripts/test_ci_workflow.py -v`

Expected: PASS。

```powershell
git add .github/workflows/ci.yml .github/dependabot.yml backend/tests/scripts/test_ci_workflow.py
git commit -m "ci: verify backend frontend and security coverage"
```

### Task 6: 增加无模型 Docker Web/API Demo

**Files:**
- Create: `backend/src/voxagent/demo/__init__.py`
- Create: `backend/src/voxagent/demo/runtime.py`
- Create: `backend/src/voxagent/demo/app.py`
- Modify: `backend/src/voxagent/cli.py`
- Modify: `frontend/src/App.tsx`
- Create: `frontend/src/demoMode.ts`
- Create: `frontend/src/demoMode.test.ts`
- Create: `deploy/docker/backend.Dockerfile`
- Create: `deploy/docker/frontend.Dockerfile`
- Create: `deploy/docker/nginx.conf`
- Create: `docker-compose.demo.yml`
- Create: `scripts/start-docker-demo.ps1`
- Test: `backend/tests/demo/test_runtime.py`
- Test: `backend/tests/demo/test_app.py`

**Interfaces:**
- CLI: `voxagent serve-demo --session-token TOKEN --host 0.0.0.0 --port 8765`
- Compose URL: 启动脚本打印带有本次内存 token 和 `demo=1` 的 `http://127.0.0.1:8780/` 地址

- [ ] **Step 1: 写 deterministic runtime 测试**

Demo 支持文字问答、固定知识搜索、一个需要确认的 fake reminder 和审计展示；禁止音频上传和真实应用启动。每个回答事件标记 `runtime_mode="deterministic_demo"`，同一输入得到同一输出。

- [ ] **Step 2: 实现 demo app**

复用真实 FastAPI/WebSocket 协议、Agent 图、Policy、Confirmation、前端确认卡和 Metrics，只替换 LLM、知识语料、工具业务服务和语音适配器。`serve-demo` 不调用 `_create_production_app()`，也不读取模型目录。

- [ ] **Step 3: 显示不可移除的演示标识**

前端从 `demo=1` 建立内存模式并立即用 `history.replaceState` 清除 query token；页面顶端始终显示“演示模式：未连接真实模型”。浏览器刷新后需要重新输入或通过启动脚本重新打开 URL，不把 token 写入 storage。

- [ ] **Step 4: 构建最小容器**

backend image 只安装生产 core dependencies，不安装 speech extra；frontend image 先构建再由 nginx 提供静态资源和 WebSocket reverse proxy。Compose 配置 `read_only: true`、`cap_drop: [ALL]`、`security_opt: [no-new-privileges:true]`、挂载到 `/data` 的 64 MiB tmpfs、`VOXAGENT_DATA_ROOT=/data`、512 MiB backend memory limit，并只发布 `127.0.0.1:8780:8080`。

- [ ] **Step 5: 实现启动脚本**

脚本用 32 个随机字节生成 URL-safe token，作为当前 `docker compose` 进程环境变量，不写 `.env` 或磁盘；构建、等待 `/healthz` 最多 60 秒，最后打印完整本地 URL。脚本不自动打开浏览器。

- [ ] **Step 6: 运行容器 smoke**

Run: `docker compose -f docker-compose.demo.yml config`

Expected: 配置合法且 published host 为 `127.0.0.1`。

Run: `powershell -ExecutionPolicy Bypass -File scripts/start-docker-demo.ps1`

Expected: `/healthz` 返回 `status=ok` 和 `runtime_mode=deterministic_demo`；文字 WebSocket smoke 成功。

- [ ] **Step 7: 提交**

```powershell
git add backend/src/voxagent/demo backend/src/voxagent/cli.py backend/tests/demo frontend/src/App.tsx frontend/src/demoMode.ts frontend/src/demoMode.test.ts deploy/docker docker-compose.demo.yml scripts/start-docker-demo.ps1
git commit -m "feat: add explicit no-model Docker demo"
```

### Task 7: 完成求职 README、架构图和故障手册

**Files:**
- Modify: `README.md`
- Modify: `docs/portfolio/current-capabilities.md`
- Modify: `docs/portfolio/demo-script.md`
- Create: `docs/portfolio/architecture.md`
- Create: `docs/portfolio/security-model.md`
- Create: `docs/portfolio/operations.md`
- Create: `docs/portfolio/known-limitations.md`
- Test: `backend/tests/scripts/test_portfolio_docs.py`

**Interfaces:**
- Five-minute paths: Docker deterministic demo、Windows real local runtime
- Evidence links: Agent eval、RAG eval、hardware baseline、CI、security tests

- [ ] **Step 1: 写文档可验证性测试**

解析 README 中的相对链接并要求目标存在；抽取 PowerShell/code commands，至少验证 `--help`、`docker compose config` 和无模型 demo smoke。搜索 `待补充`、`TBD`、`TODO`、假成绩和失效路径并失败。

- [ ] **Step 2: 绘制两张 Mermaid 图**

第一张显示 Browser→FastAPI/WS→Conversation→LangGraph→Tool Policy→native/MCP→SQLite/Ollama；第二张显示 ASR/LLM/TTS 的流式事件和 cancel 路径。每个外部进程、信任边界和持久化位置必须标注。

- [ ] **Step 3: 写安全模型和运维手册**

安全模型覆盖资产、攻击面、L0/L1/L2、路径策略、MCP capability、Prompt Injection 和非目标；运维覆盖启动、健康检查、metrics、日志、备份、恢复、端口冲突、Ollama/Reranker/MCP 降级和干净停止。

- [ ] **Step 4: 写真实限制**

明确 GTX 1660 Ti 6GB、16GB RAM、单会话、Windows-first、4K–8K context、Kokoro 延迟、Docker 仅 deterministic demo、无训练/LoRA、无多租户和无任意命令执行。

- [ ] **Step 5: 完整验证并提交**

Run: `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1 -Scope All -Coverage`

Expected: `Verification passed.`，exit 0。

```powershell
git add README.md docs/portfolio backend/tests/scripts/test_portfolio_docs.py
git commit -m "docs: publish job-ready architecture and operations guide"
```

## JR-05 Completion Gate

- JSONL 关联日志在 canary 测试中不泄露 token、capability、用户文本、参数、路径或内容。
- Metrics 无高基数标签，Gauge 在异常、取消和 shutdown 后归零，端点需要 Bearer Token。
- 安全关键模块 branch coverage 至少 85%。
- GitHub Actions 在 Windows 上运行 backend、frontend 和 security coverage，全部使用 frozen lock。
- Docker Demo 不需要模型/GPU/麦克风，只绑定 loopback，并始终显示 deterministic demo 标识。
- README 的命令、链接、架构、安全、限制和故障处理可由自动测试验证。
- `scripts/verify.ps1 -Scope All -Coverage` 通过。
