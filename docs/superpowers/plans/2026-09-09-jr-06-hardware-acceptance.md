# JR-06 目标机器验收与求职发布 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在目标 GTX 1660 Ti 6GB / 16GB RAM 电脑上完成真实 Agent、RAG、语音和稳定性验收，生成可追溯证据并整理成求职发布版本。

**Architecture:** 一个只负责编排的 PowerShell acceptance runner 依次做环境快照、全量自动测试、真实 Ollama Agent/RAG 评测、20 次人工插话、30 分钟混合负载和资源采样。原始本机结果留在数据根，脱敏聚合报告通过 JSON Schema 后进入仓库；任何门槛失败都保留报告并返回非零。

**Tech Stack:** PowerShell、Typer、pytest、Playwright/Vitest、Ollama Qwen3 4B、psutil、nvidia-smi、JSON Schema、Git

## Global Constraints

- 真实验收只在干净提交上进行；用户未提交文件、个人数据库和模型文件不进入发布提交。
- 启动完整模式前要求至少 6 GiB available RAM；4 GiB 以下直接阻止；4–6 GiB 只允许单独运行无语音 Agent/RAG 诊断。
- Qwen3 4B 独占 GPU，context 只允许 4K 或 8K，峰值 VRAM 必须不高于 5.4 GiB。
- 本地隐私音色的 TTS 首段声音 p95 目标为不高于 4 秒；未达到时标记 `voice_performance_degraded` 并披露，不能把浏览器在线音色成绩冒充本地成绩，也不因此伪造 Agent/RAG 总验收失败。
- ASR、Embedding、Reranker 用 CPU；Embedding/Reranker batch 不超过 8；同一时刻最多一个重排推理。
- 完整语音验收期间关闭 Docker Demo、其他 Ollama 模型和其他 GPU compute process。
- 报告不包含 Windows 用户名、主机名、序列号、IP、Session Token、绝对路径、用户内容或个人文档。
- deterministic/fake 成绩不能填入真实模型指标；人工验收必须记录 reviewer 和时间。
- 每个任务独立提交；最终 tag 只在全部门槛通过并经人工检查后创建。

---

### Task 1: 固化求职版硬件 preflight

**Files:**
- Modify: `backend/src/voxagent/diagnostics/hardware.py`
- Create: `backend/src/voxagent/diagnostics/job_readiness.py`
- Modify: `backend/src/voxagent/cli.py`
- Test: `backend/tests/diagnostics/test_job_readiness.py`
- Create: `qa/reports/job-readiness-hardware.schema.json`

**Interfaces:**
- CLI: `voxagent preflight-job-readiness --output PATH --mode full|agent-only`
- Status: `ready`, `degraded`, `blocked`
- Stable issue codes: `ram_below_block`, `ram_below_full`, `gpu_missing`, `vram_below_target`, `competing_gpu_process`, `disk_below_target`, `model_identity_unverified`

- [ ] **Step 1: 写阈值表测试**

测试 3.99/4.00/5.99/6.00 GiB RAM 边界、5.39/5.40 GiB 启动可用 VRAM 边界、GPU 不存在、多 GPU process、数据盘小于 10 GiB 和 agent-only 降级。浮点显示不能改变基于 bytes 的判定。

- [ ] **Step 2: 实现求职模式判定**

`full` 需要 RAM≥6 GiB、NVIDIA GPU 总 VRAM≥6 GiB 且启动可用 VRAM≥5.4 GiB、目标 Ollama digest 匹配、数据盘可用≥10 GiB、无竞争 compute process；报告另记录基准机型 GTX 1660 Ti。`agent-only` 可在 RAM≥4 GiB 且 Ollama 可运行时返回 degraded。采集失败一律 unknown，不当作 0 或 PASS。

- [ ] **Step 3: 输出脱敏快照**

只保留 OS family/version、CPU logical/physical count、RAM total/available GiB、GPU model/VRAM、磁盘 free GiB、模型 ID/digest prefix、Reranker SHA 和 issue codes。schema 额外设置 `additionalProperties: false`。

- [ ] **Step 4: 运行并提交**

Run: `cd backend; uv run pytest tests/diagnostics/test_job_readiness.py -v`

Expected: PASS。

Run: `cd backend; uv run voxagent preflight-job-readiness --mode full --output ../qa/reports/local-hardware.json`

Expected on prepared target machine: status `ready`；未关闭其他程序时允许如实返回 degraded/blocked。

```powershell
git add backend/src/voxagent/diagnostics/hardware.py backend/src/voxagent/diagnostics/job_readiness.py backend/src/voxagent/cli.py backend/tests/diagnostics/test_job_readiness.py qa/reports/job-readiness-hardware.schema.json
git commit -m "feat: gate full runtime on target hardware"
```

### Task 2: 运行真实 60 条 Agent 验收

**Files:**
- Modify: `backend/src/voxagent/agent/evaluation.py`
- Modify: `backend/src/voxagent/cli.py`
- Create: `qa/reports/agent-report.schema.json`
- Create: `docs/portfolio/agent-evaluation.md`
- Test: `backend/tests/agent/test_local_evaluation_report.py`

**Interfaces:**
- CLI: `voxagent evaluate-agent --dataset qa/scenarios/agent-tasks.json --output PATH --mode local --final`
- Gates: tool selection ≥0.85、first-pass Schema validity ≥0.95、unauthorized execution count=0

- [ ] **Step 1: 完善真实报告合同**

报告记录 commit、dirty、dataset SHA、Ollama model ID 和 digest、temperature=0、num_ctx、tool provider、每条 expected/actual tool、Schema validation attempts、permission decision、execution status、latency 和稳定失败码。用户回复原文与隐藏 Prompt 不进入提交报告。

- [ ] **Step 2: 写 hard-fail 测试**

边界值 0.849/0.850、0.949/0.950 和任意一次 unauthorized execution；只要越权执行数大于 0，其他指标再高也 exit 1。数据集不是恰好 60 条或分组数量漂移时 exit 2。

- [ ] **Step 3: 在 native 与 MCP 各跑一次安全子集**

完整 60 条使用 `native` provider；10 条越权/注入集合再使用 `mcp` provider。两次都清空临时业务数据库，写工具由评测 harness 的隔离 fake service 接收，不能创建真实提醒或打开应用。

- [ ] **Step 4: 执行目标机器评测**

Run: `cd backend; uv run voxagent evaluate-agent --dataset ../qa/scenarios/agent-tasks.json --output ../qa/reports/local-agent.json --mode local --final`

Expected: 60 cases，tool selection ≥85%，first-pass Schema validity ≥95%，unauthorized executions 0，exit 0。

- [ ] **Step 5: 生成脱敏说明并提交**

`docs/portfolio/agent-evaluation.md` 写明模型、样本构成、公式、分组结果、失败案例类别和改进方向；数字必须从 `local-agent.json` 生成，不手填。

```powershell
git add backend/src/voxagent/agent/evaluation.py backend/src/voxagent/cli.py backend/tests/agent/test_local_evaluation_report.py qa/reports/agent-report.schema.json docs/portfolio/agent-evaluation.md
git commit -m "test: publish real local agent evaluation"
```

### Task 3: 运行真实 50 条 RAG 验收

**Files:**
- Modify: `backend/src/voxagent/rag/evaluation.py`
- Create: `backend/tests/rag/test_local_evaluation_report.py`
- Modify: `docs/portfolio/rag-evaluation.md`

**Interfaces:**
- CLI: `voxagent evaluate-rag --dataset qa/scenarios/rag-questions.json --source-dir qa/fixtures/rag-source --output PATH --mode local --final`
- Gates: Recall@5 ≥0.85、MRR ≥0.75、citation accuracy ≥0.90、warm retrieval p95 <500ms

- [ ] **Step 1: 写真实/确定性隔离测试**

只有 `answer_mode="local_qwen"` 且 model digest 匹配 baseline 的报告能通过 final validator；deterministic、fake、未知 digest、Reranker 降级超过 10% 或数据集不是 50 条都不能成为发布成绩。

- [ ] **Step 2: 运行三个检索模式**

在同一个干净临时数据库导入固定 fixture，依次运行 vector、hybrid_rrf、hybrid_reranked。每种先预热一次，正式每题 3 次；报告保留聚合 ranking 和中位 latency，不把三次内容重复送给回答模型。

- [ ] **Step 3: 执行目标机器评测**

Run: `cd backend; uv run voxagent evaluate-rag --dataset ../qa/scenarios/rag-questions.json --source-dir ../qa/fixtures/rag-source --output ../qa/reports/local-rag.json --mode local --final`

Expected: 50 cases，四项核心门槛通过，exit 0。

- [ ] **Step 4: 自动生成对比文档**

文档必须展示 vector→RRF→reranked 的 Recall@5、MRR、p50/p95、citation/refusal，并明确 Reranker 模型、119MB 权重、CPU/batch 8 和 fallback 行为。指标下降也如实保留，不挑选单次最好结果。

- [ ] **Step 5: 运行并提交**

Run: `cd backend; uv run pytest tests/rag/test_local_evaluation_report.py -v`

Expected: PASS。

```powershell
git add backend/src/voxagent/rag/evaluation.py backend/tests/rag/test_local_evaluation_report.py docs/portfolio/rag-evaluation.md
git commit -m "test: publish real hybrid RAG evaluation"
```

### Task 4: 完成 20 次插话人工验收

**Files:**
- Create: `qa/manual/barge-in-protocol.md`
- Create: `qa/manual/barge-in-template.json`
- Create: `backend/src/voxagent/diagnostics/manual_review.py`
- Modify: `backend/src/voxagent/cli.py`
- Test: `backend/tests/diagnostics/test_manual_review.py`

**Interfaces:**
- Prepare: `voxagent prepare-barge-in-review --output-dir PATH --trials 20`
- Finalize: `voxagent finalize-barge-in-review --review PATH --output PATH`
- Gate: stop latency p95 ≤300ms、old audio resumed count=0、20 valid trials

- [ ] **Step 1: 固定人工协议**

在安静环境、系统音量固定、同一麦克风/扬声器和默认 Kokoro 音色下进行。10 次在首句播放早期插话、5 次在句间、5 次连续两轮；每次记录 UI/日志给出的 `speech_start_monotonic_ms`、`audio_stopped_monotonic_ms`、旧音频是否恢复和 reviewer 判定。

- [ ] **Step 2: 写模板验证器**

要求 trial ID 1–20 唯一，两个时间单调且 latency 非负，reviewer 非空，所有 trial 都有 `old_audio_resumed`。修改时间、缺 trial、复制 ID 或 reviewer 未确认均 exit 2。

- [ ] **Step 3: 增加观测按钮**

prepare 命令生成只含测试句、步骤和空 reviewer 字段的模板；人工在 UI 完成后只填写观察值。finalize 从原始 20 条计算 p50/p95/max 和恢复次数，不允许输入者直接填写聚合指标。

- [ ] **Step 4: 执行与复核**

Run: `cd backend; uv run voxagent prepare-barge-in-review --output-dir ../qa/reports/local-barge-in --trials 20`

人工完成模板后运行：

Run: `cd backend; uv run voxagent finalize-barge-in-review --review ../qa/reports/local-barge-in/review.json --output ../qa/reports/local-barge-in-summary.json`

Expected: 20 valid trials，p95≤300ms，old audio resumed=0，exit 0。

- [ ] **Step 5: 提交协议和验证器**

```powershell
git add qa/manual/barge-in-protocol.md qa/manual/barge-in-template.json backend/src/voxagent/diagnostics/manual_review.py backend/src/voxagent/cli.py backend/tests/diagnostics/test_manual_review.py
git commit -m "test: formalize twenty-trial barge-in review"
```

### Task 5: 完成 30 分钟混合负载和资源验收

**Files:**
- Create: `backend/src/voxagent/diagnostics/job_soak.py`
- Modify: `backend/src/voxagent/cli.py`
- Create: `qa/scenarios/soak-sequence.json`
- Test: `backend/tests/diagnostics/test_job_soak.py`
- Create: `qa/reports/soak-report.schema.json`

**Interfaces:**
- CLI: `voxagent soak-job-readiness --duration-minutes 30 --scenario PATH --output PATH`
- Gates: crash=0、database integrity=`ok`、unbounded queue=0、VRAM≤5.4GiB、available RAM never <1GiB；同时记录 local TTS first-audio p95

- [ ] **Step 1: 定义混合序列**

每个循环固定包含：2 个普通文本 turn、1 个知识查询、1 个只读提醒查询、1 个经隔离确认的提醒创建、1 个 15–30 字 TTS、1 个取消、新增并删除一份 fixture 文档。所有写操作在临时验收数据库中执行。

- [ ] **Step 2: 写可缩短的 harness 测试**

单元测试用 3 秒 fake clock 跑至少两个循环，验证失败仍写报告、SIGINT/Cancel 保存 partial、队列峰值记录、数据库 `PRAGMA integrity_check` 和资源采样间隔 500ms。

- [ ] **Step 3: 实现实时门槛**

若 available RAM <1 GiB、VRAM >5.4 GiB、任一队列连续 10 秒保持容量上限、数据库 integrity 非 ok 或模型 digest 变化，立即停止新 work，等待最多 5 秒在途任务，写失败报告并 exit 1；脚本不结束 Ollama 或其他用户进程。30 分钟结束后从真实本地 TTS 样本计算首段声音 p95；超过 4 秒时报告 `voice_performance_degraded`，但保留其他 gate 的独立结果。

- [ ] **Step 4: 执行真机 soak**

Run: `cd backend; uv run voxagent soak-job-readiness --duration-minutes 30 --scenario ../qa/scenarios/soak-sequence.json --output ../qa/reports/local-soak.json`

Expected: 时长≥1800 秒，至少 20 个完整循环，无崩溃、无 integrity 错误、无无界队列，资源门槛通过。

- [ ] **Step 5: 运行测试并提交**

Run: `cd backend; uv run pytest tests/diagnostics/test_job_soak.py -v`

Expected: PASS。

```powershell
git add backend/src/voxagent/diagnostics/job_soak.py backend/src/voxagent/cli.py backend/tests/diagnostics/test_job_soak.py qa/scenarios/soak-sequence.json qa/reports/soak-report.schema.json
git commit -m "test: exercise thirty-minute mixed local workload"
```

### Task 6: 编排最终验收并生成脱敏证据

**Files:**
- Create: `scripts/run-job-readiness-acceptance.ps1`
- Create: `backend/src/voxagent/diagnostics/acceptance_report.py`
- Modify: `backend/src/voxagent/cli.py`
- Create: `qa/reports/job-readiness-report.schema.json`
- Create: `backend/tests/diagnostics/test_acceptance_report.py`
- Create: `docs/portfolio/job-readiness-report.md`

**Interfaces:**
- Runner: `scripts/run-job-readiness-acceptance.ps1 -DataRoot D:\VoxAgentData -OutputDirectory qa/reports/local-final`
- Aggregator: `voxagent finalize-job-readiness --input-dir PATH --output-json PATH --output-markdown PATH`

- [ ] **Step 1: 写 fail-closed 聚合测试**

缺少任一报告、commit 不一致、dirty 为 true、Schema 失败、人工验收不完整或任一硬门槛失败时，总状态必须 FAIL。只有全部证据相同 commit 且全绿时为 PASS。

- [ ] **Step 2: 实现顺序 runner**

顺序固定为：hardware preflight→`verify.ps1 -Coverage`→Ollama identity→真实 Agent→真实 RAG→等待人工 barge-in 文件→30 分钟 soak→aggregate。每步写 UTC start/end、exit code 和 artifact SHA；失败后跳过依赖步骤但仍执行 aggregator。

- [ ] **Step 3: 生成仓库可提交摘要**

JSON/Markdown 只保留硬件档位、模型标识、聚合指标、pass/fail、已知限制、Git commit 和证据文件 SHA。原始逐题记录、日志和人工明细留在 `local-final` 且受 `.gitignore` 保护。

- [ ] **Step 4: 执行完整验收**

Run: `powershell -ExecutionPolicy Bypass -File scripts/run-job-readiness-acceptance.ps1 -DataRoot D:\VoxAgentData -OutputDirectory qa/reports/local-final`

Expected: 全部通过后打印 `JOB READINESS: PASS`；总时长约 45–75 分钟。

- [ ] **Step 5: 独立复核摘要**

Run: `cd backend; uv run pytest tests/diagnostics/test_acceptance_report.py -v`

Expected: PASS。

Run: `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1 -Scope All -Coverage`

Expected: `Verification passed.`，exit 0。

- [ ] **Step 6: 提交**

```powershell
git add scripts/run-job-readiness-acceptance.ps1 backend/src/voxagent/diagnostics/acceptance_report.py backend/src/voxagent/cli.py backend/tests/diagnostics/test_acceptance_report.py qa/reports/job-readiness-report.schema.json docs/portfolio/job-readiness-report.md
git commit -m "test: aggregate target-machine job readiness evidence"
```

### Task 7: 整理求职发布和面试材料

**Files:**
- Modify: `README.md`
- Create: `docs/portfolio/resume-bullets.md`
- Create: `docs/portfolio/interview-walkthrough.md`
- Create: `docs/portfolio/release-checklist.md`
- Create: `CHANGELOG.md`
- Test: `backend/tests/scripts/test_release_readiness.py`

**Interfaces:**
- Release candidate: `job-ready-v1.0.0`
- Interview walkthrough: 3 分钟、10 分钟、30 分钟三个版本

- [ ] **Step 1: 写发布阻断测试**

检查 README 所有命令/链接、最终报告 PASS 和 commit 一致、工作树无非预期文件、无 token/path canary、版本号一致、全部 tracked artifact 小于 10 MiB。发现原始日志、数据库、模型或音频立即失败。

- [ ] **Step 2: 写可量化简历 bullet**

只使用最终报告中的真实数字，覆盖安全 Agent、MCP、Hybrid RAG、语音流式取消、评测/CI 和硬件优化；每条包含动作、技术、规模和结果，不写“精通”“行业领先”或未验证吞吐。

- [ ] **Step 3: 写面试讲解**

3 分钟讲业务闭环；10 分钟讲架构、Tool Policy、MCP capability、RRF/Reranker 和指标；30 分钟加入失败路径、测试策略、硬件取舍、Kokoro 瓶颈和若扩展到生产的下一步。

- [ ] **Step 4: 最终验证**

Run: `cd backend; uv run pytest tests/scripts/test_release_readiness.py -v`

Expected: PASS。

Run: `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1 -Scope All -Coverage`

Expected: `Verification passed.`，exit 0。

- [ ] **Step 5: 提交 release candidate**

```powershell
git add README.md CHANGELOG.md docs/portfolio/resume-bullets.md docs/portfolio/interview-walkthrough.md docs/portfolio/release-checklist.md
git commit -m "docs: prepare junior agent engineer portfolio release"
```

- [ ] **Step 6: 人工检查后创建 tag**

确认 `git status --short` 为空、`job-readiness-report.md` 为 PASS、演示脚本可运行后执行：

```powershell
git tag -a job-ready-v1.0.0 -m "VoxAgent junior Agent engineer portfolio release"
git show --stat job-ready-v1.0.0
```

Expected: tag 指向最后一个求职发布提交；本计划不自动 push。

## JR-06 Completion Gate

- full preflight 为 ready；若只达到 agent-only degraded，不能宣称完整语音验收通过。
- 60 条真实 Agent：tool selection≥85%、first-pass Schema validity≥95%、unauthorized execution=0。
- 50 条真实 RAG：Recall@5≥85%、MRR≥0.75、citation accuracy≥90%、warm p95<500ms。
- 20 次人工插话：stop latency p95≤300ms、旧音频恢复=0。
- 30 分钟混合负载：无崩溃、数据库 integrity ok、无无界队列，VRAM/RAM 门槛通过；本地 TTS 首段声音 p95 已实测并在超过 4 秒时明确降级。
- 安全关键 branch coverage≥85%，backend/frontend/build 全绿。
- 最终摘要脱敏、Schema 合法、全部证据来自同一干净 commit。
- README、简历 bullet、面试 walkthrough、限制和 release checklist 完整。
