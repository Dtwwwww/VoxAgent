# 声灵：初级大模型应用 / Agent 工程师求职增强路线图

**日期：** 2026-09-09

**设计依据：** [`../specs/2026-09-09-junior-agent-job-readiness-design.md`](../specs/2026-09-09-junior-agent-job-readiness-design.md)

**岗位差距依据：** [`../../portfolio/hangzhou-junior-agent-role-gap-analysis.md`](../../portfolio/hangzhou-junior-agent-role-gap-analysis.md)

**计划周期：** 4–6 周

**当前基线提交：** `3e1d3e2`

## 1. 路线目标

本路线优先让项目具备初级大模型应用 / Agent 工程师岗位所要求的可运行证据：安全的 Agent 工具闭环、MCP、Hybrid RAG、可量化评测、可观测性和可复现交付。它不以完成声灵最初规划的所有桌面产品功能为目标。

## 2. 与原 Plan 01–06 的关系

| 原计划 | 当前状态 | 本路线处理方式 |
|---|---|---|
| Plan 01：运行时与模型基准 | 已完成 | 保留，继续作为硬件证据基础 |
| Plan 02：流式语音闭环 | 已完成主体，仍有前端回归和人工验收缺口 | 由 JR-01 修复，由 JR-06 完成人工验收 |
| Plan 03：记忆与本地知识 | 已完成主体 | 由 JR-04 升级检索与评测 |
| Plan 04：安全本地工具 | 尚未实现 | 由 JR-02 与 JR-03 取代其第一版实现顺序；原计划保留作完整产品参考 |
| Plan 05：Electron 桌面产品化 | 尚未实现 | 延后到求职增强完成后，不是 4–6 周硬门槛 |
| Plan 06：完整发布验收 | 尚未实现 | 由 JR-05/JR-06 先完成求职版子集；完整安装包、SBOM 和卸载测试仍留在原计划 |

新计划不会删除或改写原 Plan 04–06；当要求冲突时，4–6 周求职范围以本路线和已批准设计为准。

## 3. 执行顺序

```text
JR-01 求职基线整备
   |
   v
JR-02 安全 Agent 工具闭环
   |
   v
JR-03 MCP 互操作
   |
   v
JR-04 Hybrid RAG 与评测
   |
   v
JR-05 可观测性与交付
   |
   v
JR-06 目标机器验收与求职发布
```

每一阶段必须通过完成门槛才能进入下一阶段。JR-02 和 JR-04 不并行修改会话上下文或数据库迁移，以避免共享状态冲突。

## 4. 计划索引

1. [`2026-09-09-jr-01-portfolio-baseline.md`](2026-09-09-jr-01-portfolio-baseline.md)
   - 修复 36 个前端失败测试。
   - 固化“默认仅本地、在线模式显式选择”的产品契约。
   - 增加统一验证入口、仓库清洁检查和求职版 README 基线。

2. [`2026-09-09-jr-02-safe-agent-tools.md`](2026-09-09-jr-02-safe-agent-tools.md)
   - LangGraph 1.2 单 Agent 状态图。
   - Ollama/Qwen3 流式 Tool Calling。
   - 工具注册、权限、确认、防重放、审计和前端确认卡。
   - 四类真实本地工具与 60 条 Agent 评测集骨架。

3. [`2026-09-09-jr-03-mcp-interoperability.md`](2026-09-09-jr-03-mcp-interoperability.md)
   - MCP Python SDK 2.2 本地 stdio Server/Client。
   - 工具发现、统一适配、超时、输出限制和生命周期。
   - 证明 MCP 不绕过本地权限与确认边界。

4. [`2026-09-09-jr-04-hybrid-rag-evaluation.md`](2026-09-09-jr-04-hybrid-rag-evaluation.md)
   - SQLite FTS5/BM25、BGE 向量、RRF 与多语言 MiniLM INT8 Reranker。
   - 50 条中文问答集。
   - Recall@5、MRR、引用正确率、拒答和延迟对比报告。

5. [`2026-09-09-jr-05-observability-delivery.md`](2026-09-09-jr-05-observability-delivery.md)
   - 脱敏 JSON 日志、Prometheus Metrics、关联 ID。
   - GitHub Actions 和统一验证报告。
   - 不包含真实麦克风和 GPU 的 Docker Web/API 演示模式。
   - README、架构图和演示脚本。

6. [`2026-09-09-jr-06-hardware-acceptance.md`](2026-09-09-jr-06-hardware-acceptance.md)
   - Agent/RAG 正式评测。
   - 20 次插话与 30 分钟混合负载。
   - 资源、延迟和失败恢复证据。
   - 求职发布清单与面试讲解材料。

## 5. 建议日程

| 周次 | 主要工作 | 周末可交付物 |
|---|---|---|
| 第 1 周 | JR-01 | 干净、全绿、可复现的项目基线 |
| 第 2 周 | JR-02 前半 | Tool Schema、权限、确认、Ollama Tool Calling |
| 第 3 周 | JR-02 后半 + JR-03 | 完整工具闭环、前端确认卡、本地 MCP |
| 第 4 周 | JR-04 | Hybrid RAG、Reranker 和评测报告 |
| 第 5 周 | JR-05 | 日志、Metrics、CI、Docker Demo、README |
| 第 6 周 | JR-06 与缓冲 | 目标机器验收、演示视频、发布标签和面试材料 |

如果前一阶段超过预算，按以下顺序缩减：

1. 保留 LangGraph、权限确认、三种工具、MCP、Hybrid RAG 和评测。
2. 可把白名单应用打开从首批演示中移除，但保留策略测试。
3. 可把 Docker 演示降为仅后端 Mock-LLM 模式。
4. 不削减安全负例、测试全绿、Agent/RAG 指标或 README 证据。

## 6. 总体完成门槛

- `main` 或最终求职分支包含全部已展示能力，工作树无非预期改动。
- 后端 pytest、Ruff、前端 Vitest、TypeScript 和 Vite 构建全部通过。
- Tool Policy、Confirmation、路径策略和 MCP 边界分支覆盖率至少 85%。
- 60 条 Agent 集达到工具选择准确率至少 85%、首次参数合法率至少 95%。
- 未确认写操作、越权路径、未知工具的实际执行数均为 0。
- 50 条 RAG 集达到 Recall@5 至少 85%、MRR 至少 0.75、引用正确率至少 90%。
- 目标机器完整模式启动时至少有 6GB 可用 RAM；峰值 VRAM 不超过 5.4GB。
- 20 次插话无旧音频恢复，停止播放 p95 不超过 300ms。
- 30 分钟混合负载无崩溃、无数据库损坏、无无界队列。
- README、架构、演示、评测、限制和复现步骤完整。

## 7. 硬件结论

现有提交证据不是纸面估算：

| 项目 | 已有真机结果 | 对本路线的含义 |
|---|---:|---|
| CPU | i5-9300H，4 核 8 线程 | 能做 ASR、Embedding 和小型 Reranker，但必须限制 batch/并发 |
| 系统内存 | 总计 15.88GB，基准快照可用 8.37GB | 满足完整模式 6GB 启动线；开发时需关闭其他高内存程序 |
| GPU | GTX 1660 Ti 6GB，快照可用 5966MB | Qwen3 4B 可独占运行，不能叠加多个大模型 |
| Qwen3 4B | 峰值 VRAM 3783MiB，TTFT p95 0.306s | 足以承担 Agent Tool Calling；仍需 JR-06 的 30 分钟新 soak |
| Qwen3.5 4B | 约 431 秒时因系统内存压力提前停止 | 不选为默认模型，不能用短时质量优势覆盖稳定性失败 |
| SenseVoice | final ASR p95 1.095s，RTF p95 0.073 | CPU 语音识别具备实时余量 |
| Kokoro | 整句合成 10.909s；一次流式首 token 到音频 3.919s | 能运行但体验边缘，必须保留降级音色并复测 p95 |
| 数据盘 | D 盘基准快照可用 283.53GB | 足够存放现有模型、119MB Reranker 和验收产物 |

目标电脑可以完成并运行本路线，条件是：

- Qwen3 4B 独占 GPU，维持 4K–8K 上下文。
- ASR、Embedding 和 Reranker 使用 CPU，Embedding/Reranker 批量最多 8。
- 不同时运行完整语音模式和 Docker 无模型演示栈。
- 启动前关闭其他大模型服务和高内存应用，至少释放 6GB RAM。
- Reranker 使用约 119MB 的多语言 MiniLM ONNX INT8 权重；失败时退回 RRF。

本路线不要求 70B 模型、多 Agent、本地训练或 Kubernetes，因此计算能力不是实施阻塞项。现有 Kokoro TTS 延迟是体验风险，但不会阻塞 Agent、MCP、RAG 和工程化求职能力的实现。

## 8. 执行纪律

- 每项功能先写失败测试，再写最小实现。
- 每个任务结束运行其定向测试，每个计划结束运行统一验证。
- 每个任务独立提交，不把用户本地模型、录音、数据库、令牌或绝对私有路径加入 Git。
- 新依赖写入 `pyproject.toml` 并更新 `uv.lock`；前端依赖写入 `package.json` 和 `pnpm-lock.yaml`。
- 所有评测报告记录构建提交、UTC 时间、模型 ID、配置和目标机器快照。
- 自动化证据和人工验收明确区分，不能用 Mock 结果冒充真实模型或真实麦克风结果。
