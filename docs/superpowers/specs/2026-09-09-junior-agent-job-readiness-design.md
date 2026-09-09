# 声灵：初级大模型应用 / Agent 工程师求职增强设计

**日期：** 2026-09-09

**状态：** 已确认设计，等待书面审阅

**目标周期：** 4–6 周

**目标平台：** Windows 11 单机、单用户，i5-9300H、16GB RAM、GTX 1660 Ti 6GB

**目标岗位：** 初级大模型应用工程师、初级 AI Agent 工程师、语音 AI 应用工程师

## 1. 目标与判定标准

本轮增强把声灵从“具备实时语音、长期记忆和基础本地检索的 AI 应用”提升为“可展示完整 Agent 闭环、RAG 评测和工程化交付能力的求职主项目”。完成后，候选人应能用该项目证明以下能力：

- 使用 Python、FastAPI、WebSocket 和异步任务开发大模型应用后端。
- 使用 LangGraph 构建有边界、可恢复、可追踪的单 Agent 工作流。
- 实现流式 Tool Calling、严格参数校验、权限分级、用户确认和真实结果回填。
- 理解并实现 MCP 工具发现与调用，而不是只会配置第三方 MCP 服务。
- 实现 BM25、向量召回、RRF 和轻量 Reranker 组成的混合 RAG。
- 用固定数据集和指标评估 Agent、RAG、延迟、安全性与回归。
- 使用 CI、结构化日志、Metrics 和可选 Docker 演示模式交付项目。
- 解释本地模型的性能、资源、安全和降级取舍。

完成标准不是“代码中出现招聘关键词”，而是每项能力均有可运行实现、自动测试、机器可读评测结果和可复现演示。

## 2. 当前基线

当前 `phase/03-memory-and-knowledge` 分支已经具备：

- Ollama/Qwen3 本地流式推理和目标机器基准。
- FastAPI HTTP/WebSocket 服务和本机 Session Token 鉴权。
- VAD、SenseVoice/Paraformer ASR、端点判断、TTS、播放和插话取消。
- 以 `session_id`、`turn_id` 和请求序号隔离过期异步结果。
- React/TypeScript 前端及文字、语音、人格、记忆和知识库界面。
- SQLite 数据迁移、对话持久化、备份、导出和完整清空。
- BGE-small-zh-v1.5 ONNX 嵌入、NumPy 余弦召回和引用来源。
- TXT、Markdown、文本 PDF 和 DOCX 的本地导入。
- 后端 435 个测试被收集，434 个通过、1 个按条件跳过；Ruff 通过。
- TypeScript 类型检查和 Vite 生产构建通过。

当前阻塞项：

- 前端 241 个测试中有 36 个失败，主要集中在默认本地语音、分句合并和取消/回退预期。
- 最新能力尚未合并到 `main`，工作树还包含未提交文件和本地评测目录。
- 没有可执行工具、Tool Calling、MCP、LangGraph 或完整 Agent 状态图。
- RAG 只有向量召回，没有 BM25、融合、重排序和效果评测。
- 没有 CI、统一验证入口、结构化可观测性或独立 Docker 演示模式。
- 本地 Kokoro 实测整句合成约 10.9 秒，语音首段延迟仍是主要体验风险。
- 物理麦克风、20 次插话和 30 分钟混合负载验收尚未完成。

## 3. 范围

### 3.1 必须实现

1. 修复现有前端回归，整理分支、文档和可复现启动入口。
2. 引入 LangGraph 单 Agent 状态图，但保留现有语音会话状态机作为输入输出适配层。
3. 扩展 Ollama 客户端以支持 Qwen3 流式 Tool Calling 和工具结果消息。
4. 实现封闭式工具注册表、Pydantic Schema、权限策略、确认票据和审计。
5. 提供知识库查询、授权目录文件搜索、待办/提醒和白名单应用打开四类工具。
6. 实现一个本地 stdio MCP Server 和 MCP Client，复用统一工具接口。
7. 把 RAG 升级为 SQLite FTS5/BM25 + BGE 向量召回 + RRF + 小型 ONNX Reranker。
8. 建立 Agent 与 RAG 固定评测集、指标计算、基线对比和回归门槛。
9. 增加 JSON 日志、敏感信息脱敏、Prometheus Metrics 和关联 ID。
10. 增加统一 CI、Web/API Docker 演示模式、求职 README、架构图、演示脚本和结果报告。

### 3.2 明确不做

- 多 Agent 协作、群聊或自治 Agent 集群。
- 模型训练、SFT、LoRA、RLHF、蒸馏或量化训练。
- Kubernetes 集群、服务网格或公网生产部署。
- 任意 Shell、PowerShell、Python 或代码解释器工具。
- 文件删除、文件移动、软件安装、网页表单提交或无确认写操作。
- 多租户、用户注册、计费、企业级权限系统。
- Milvus、Elasticsearch 等常驻大型检索集群。
- 把 Docker 作为麦克风、GPU Ollama 或桌面运行时的默认执行环境。

## 4. 总体架构

```text
文字输入 / 实时语音
          |
          v
FastAPI HTTP + WebSocket
          |
          v
现有 ConversationOrchestrator
          |
          v
LangGraph AgentGraph
  route -> retrieve -> decide_tool -> authorize
                              |           |
                              |           +-> await_confirmation
                              |                         |
                              +-------------------------+
                                                        v
                                                execute_tool
                                                        |
                                                        v
                                            validate_result -> respond
                                                        |
                                                        v
                                         persist / audit / metrics
```

现有 `ConversationOrchestrator` 继续负责 VAD、ASR、TTS、会话生命周期、播放和取消，不承担工具策略与 RAG 决策。新的 Agent 层只消费规范化 `UserTurn`，并输出文本增量、上下文来源、确认请求、工具状态和最终结果事件。

Agent 状态图必须是有界工作流：每轮最多调用 3 次工具，最多经过 8 个图节点。代码负责工具白名单、权限、确认、循环和超时；模型只负责在已给 Schema 内提出调用及生成最终语言。

## 5. Agent 组件

### 5.1 Agent 状态

`AgentState` 至少包含：

- `session_id: str`
- `turn_id: int`
- `user_text: str`
- `messages: tuple[AgentMessage, ...]`
- `retrieval_query: str | None`
- `context_sources: tuple[ContextSource, ...]`
- `pending_tool_call: ToolCall | None`
- `tool_results: tuple[ToolResult, ...]`
- `confirmation_id: str | None`
- `tool_call_count: int`
- `node_visit_count: int`
- `final_text: str | None`
- `error_code: str | None`

状态只能包含可序列化数据，不保存数据库连接、模型 Session、文件句柄或协程对象。

### 5.2 图节点

- `route`：确定普通聊天、检索增强或动作请求。
- `retrieve`：构建检索查询并调用混合 RAG。
- `decide_tool`：向 Ollama 提供当前可用工具 Schema，解析流式调用。
- `authorize`：验证工具存在、参数合法、路径授权、权限级别和循环上限。
- `await_confirmation`：持久化确认票据并暂停本轮图执行。
- `execute_tool`：只使用已验证参数执行注册工具或 MCP 工具。
- `validate_result`：把执行器异常统一转换为结构化 `ToolResult`。
- `respond`：依据真实工具结果和引用生成最终回答。
- `persist`：以事务保存消息、来源、工具审计和完成状态。

确认后从持久化检查点恢复同一 `session_id + turn_id`，不得启动新的模型决策来替换已确认参数。

### 5.3 模型与 Tool Calling

默认模型继续使用 `qwen3:4b-instruct-2507-q4_K_M`，关闭思考模式，上下文保持 4K–8K。Ollama 客户端使用原生 `tools` 字段和 `tool_calls` 响应；工具结果以 `tool` 角色回填。

4B 模型的输出不作为安全边界。任何模型生成内容必须经过：

1. JSON 解析。
2. 工具名白名单。
3. Pydantic 严格 Schema，拒绝额外字段。
4. 业务规则与路径策略。
5. 权限等级与确认状态。

非法调用只允许把结构化错误反馈给模型一次；第二次仍失败则结束工具路径并返回可理解的错误，不执行动作。

## 6. 工具与权限

### 6.1 统一接口

原生工具和 MCP 工具统一转换为：

- `ToolDefinition`：名称、描述、输入 Schema、权限等级、超时和提供方。
- `ToolCall`：调用 ID、工具名、参数、请求哈希和关联轮次。
- `ToolResult`：状态、结构化数据、用户可见摘要、错误码和耗时。

注册表在启动时冻结。运行中不能由模型新增工具、修改权限或覆盖同名工具。

### 6.2 第一批工具

| 工具 | 权限 | 行为 |
|---|---|---|
| `knowledge.search` | L0 | 查询已导入知识库，返回带来源片段 |
| `files.search_authorized` | L0 | 只搜索用户明确授权目录，返回元数据，不读取未授权内容 |
| `reminders.list` | L0 | 查询本地待办与提醒 |
| `reminders.create` | L2 | 创建待办或提醒，必须确认 |
| `reminders.complete` | L2 | 完成指定待办，必须确认 |
| `apps.open_allowlisted` | L1 | 打开设置中允许的应用，必须确认 |

任何工具都不能接受原始命令字符串。应用工具只接受稳定的白名单 ID，执行路径由可信配置解析。

### 6.3 确认票据

确认票据绑定：

- `session_id`
- `turn_id`
- `tool_name`
- 规范化参数的 SHA-256
- 创建时间和到期时间
- 单次使用状态

新轮次、参数变化、用户拒绝、服务重启、票据过期或首次使用都会使票据不可再次使用。写工具失败后不能自动重试。

## 7. MCP

实现一个仅本地 stdio MCP Server，提供 `knowledge.search` 和 `reminders.list/create/complete`。它使用现有数据库和策略层，不建立第二套业务逻辑。

MCP Client 负责：

- 启动固定入口的本地 Server，禁止模型提供启动命令。
- 发现工具并转换为 `ToolDefinition`。
- 对工具调用设置超时、输出大小和并发限制。
- 把 MCP 错误映射为稳定 `ToolResult.error_code`。
- 在后端退出时关闭子进程并清理未完成请求。

只读 MCP 工具超时可以重试一次；写工具永不自动重试。MCP 不绕过本地 Tool Policy 和确认系统。

## 8. 混合 RAG

### 8.1 索引与查询

- 文档导入继续产生稳定的 `document_id`、`chunk_id` 和来源元数据。
- 同一事务写入文档表、向量和 FTS5 索引；任一步失败均不发布半成品。
- BM25 与向量召回各取最多 20 个候选。
- RRF 使用固定 `k=60` 融合排名。
- 融合后前 10 个候选进入小型 ONNX Reranker。
- 最终最多向上下文注入 4 个片段，继续遵循字符和 Token 上限。
- Reranker 不可用、超时或内存压力过高时，使用 RRF 结果继续回答。

### 8.2 评测

仓库提交至少 50 条不含个人隐私的中文问答，每条包含：问题、相关文档 ID、相关片段 ID 和允许的答案要点。分别评测：

1. 纯向量召回。
2. 纯 BM25。
3. BM25 + 向量 + RRF。
4. Hybrid + Reranker。

必需指标：Recall@5、MRR、引用正确率、无答案拒答准确率和检索 p50/p95。不得只提交最终最好结果而删除其他基线。

## 9. 前端交互

前端只增加求职闭环必需界面：

- 工具确认卡展示工具、用户可理解的影响、规范化参数摘要、允许和拒绝按钮。
- 确认卡显示有效、已拒绝、已过期、执行中、成功和失败状态。
- 工具状态必须来自服务端事件，不根据模型文本推断。
- 审计面板按时间展示工具、权限、确认、结果、耗时和错误码，不展示令牌和绝对私有路径。
- 现有来源引用继续展示 RAG 与记忆来源。

不建设复杂图编辑器、Agent 配置市场或可视化运维大屏。

## 10. 可观测性与交付

结构化日志包含：UTC 时间、级别、事件名、`session_id` 哈希、`turn_id`、图节点、工具名、结果状态和耗时。日志禁止记录 Session Token、完整提示词、完整文档、密码、证件号和原始音频。

Prometheus Metrics 至少包括：

- HTTP/WebSocket 请求数、错误数和耗时。
- Agent 节点耗时、工具选择、调用结果和循环中止数。
- BM25、向量、Reranker 耗时和回退次数。
- ASR、LLM TTFT、TTS 首段延迟和插话停止延迟。
- 队列深度、活动会话数和模型初始化状态。

CI 在 Windows 或跨平台可运行部分执行：

1. 后端 pytest。
2. Ruff。
3. 前端 Vitest。
4. TypeScript 类型检查。
5. Vite 生产构建。
6. Agent/RAG 小型确定性回归集。
7. Docker Web/API 演示镜像构建。

完整语音模型、GPU 和物理麦克风测试保留为目标机器验收，不在普通 CI 中下载或运行。

## 11. 容错与安全

- Tool Calling 非法：反馈一次结构化错误；第二次失败则安全终止。
- 用户拒绝或确认过期：记录 `denied` 或 `expired`，不自动再次弹出。
- MCP 超时：只读调用重试一次；写调用不重试。
- Reranker 失败：退回 RRF，不阻塞回答。
- Ollama 显存不足：先降低上下文，仍失败则提示切换 1.7B；不自动使用云模型。
- 数据库写失败：事务回滚，回复不得声称已经保存或执行。
- TTS 失败：保留文字结果并恢复监听，不改变 Agent 完成状态。
- 日志或 Metrics 写入失败：本地告警但不阻塞主流程。
- 路径策略必须处理规范化、路径穿越、符号链接和 Junction 越界。
- 检索文档、记忆、MCP 输出和工具结果均视为不可信数据，不能覆盖系统策略。

## 12. 资源预算与目标机器结论

已有基准显示 Qwen3 4B 推理峰值约 3.8GB 显存，SenseVoice 峰值约 1GB 进程内存，目标机器具备实现本设计的基础。

运行规则：

- 完整模式启动前至少需要 6GB 可用系统内存；低于 4GB 时阻止启动。
- LLM 上下文限制在 4K–8K，不提供 32K/64K 本地档位。
- Embedding 与 Reranker 批量大小最多为 8。
- 同一时间只执行一个重型 ONNX 推理任务。
- Reranker 模型文件目标小于 150MB，并只使用 CPUExecutionProvider。
- Docker/pgvector 只作为独立演示配置，不与完整语音模式同时运行。
- 开发和验收时关闭其他模型服务、过多浏览器页和高内存应用。

可实现并可在目标机器运行：LangGraph、Tool Calling、MCP、SQLite FTS5、RRF、小型 Reranker、评测、CI、日志和 Metrics。

不适合目标机器：多 Agent 并发、70B 模型、本地微调、多个大型模型常驻和大型向量数据库集群。

本地 TTS 是主要性能风险。增强版目标为首段声音 p95 不超过 4 秒、插话停止 p95 不超过 300ms。若本地 TTS 不能达到首段目标，产品保留“系统/浏览器快速音色”和“完全本地隐私音色”两档，并在性能报告中披露差异，不能伪造达标。

## 13. 验收门槛

### 13.1 Agent

- 固定 60 条任务集覆盖闲聊、RAG、工具、连续调用、拒绝和异常。
- 工具选择准确率至少 85%。
- 工具参数首次 Schema 合法率至少 95%。
- 未确认写操作、越权路径和未知工具实际执行数为 0。
- 单轮工具调用不超过 3 次，图节点访问不超过 8 次。
- 每个失败案例保存脱敏输入、节点轨迹和错误原因。

### 13.2 RAG

- 至少 50 条带标准来源的中文问答。
- Recall@5 至少 85%。
- MRR 至少 0.75。
- 引用来源正确率至少 90%。
- 热启动检索 p95 小于 500ms；报告必须注明语料规模和目标机器。

### 13.3 安全和工程

- 当前前端 36 个失败测试全部归零。
- 后端、前端、类型检查、Ruff、生产构建和 CI 全部通过。
- `tools/policy.py`、`tools/confirmation.py`、路径策略和 MCP 边界的分支覆盖率至少 85%。
- 20 次插话无旧音频恢复，停止播放 p95 不超过 300ms。
- 30 分钟混合负载无崩溃、无未授权执行、无无界队列和无数据库损坏。
- README 可由新环境按一个主入口启动，包含架构、演示、测试、评测和已知限制。

## 14. 实施分解

由于本设计包含多个可独立审查的子系统，后续不得写成单个巨型实施计划。依次生成并执行以下计划：

1. **求职基线整备：** 修复前端回归、统一配置和文档、整理分支与一键验证。
2. **安全 Agent 工具闭环：** LangGraph、Ollama Tool Calling、工具注册、权限、确认、审计和前端确认卡。
3. **MCP 互操作：** 本地 MCP Server、Client、统一适配、生命周期和安全测试。
4. **Hybrid RAG 与评测：** FTS5/BM25、RRF、Reranker、固定数据集和对比报告。
5. **可观测性与交付：** JSON 日志、Metrics、CI、Docker 演示、README 和架构/演示材料。
6. **最终硬件验收：** Agent/RAG 指标、语音延迟、20 次插话、30 分钟混合负载和求职发布包。

每个计划都必须产生可运行、可测试、可单独提交的增量；前一计划的完成门槛是后一计划的开始条件。
