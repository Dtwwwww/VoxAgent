# 杭州初级大模型应用 / Agent 岗位与声灵项目差距

**调研日期：** 2026-09-09

**适用目标：** 初级大模型应用工程师、Agent 应用工程师、语音 AI 应用工程师

**说明：** 这是基于公开岗位样本的项目能力分析，不代表杭州全部岗位，也不替代学历、实习经历和面试表现。

## 1. 当前招聘信号

公开岗位里反复出现的能力可以归成六组：

1. **Python 后端与工程基础。** Python、FastAPI/Flask/Django、API、异步、数据库、Git 是应用岗基础。
2. **Agent 工作流。** Function Calling、工具编排、状态/记忆、LangChain/LangGraph，以及调用次数、失败恢复和可控性。
3. **RAG 全链路。** 文档解析、Chunk、Embedding、向量检索、混合检索、Reranking、引用和知识更新。
4. **评测与可观测性。** 准确率、延迟、稳定性、Badcase、回归集、Logs/Metrics/Tracing 和成本意识。
5. **MCP 与工具生态。** MCP 正从加分项变成部分 Agent 岗的显式要求，但协议本身不能代替权限和安全设计。
6. **交付能力。** Docker、Linux/Shell、CI/CD、故障排查、文档和业务落地说明。

样本依据：

- 海康威视杭州大模型应用岗位明确要求 Agent 核心开发、准确率/延迟/稳定性优化、自动评测、可观测和 Badcase 闭环；该岗位偏中高级，可作为能力上限参考：[海康威视研究院岗位](https://talent.hikvision.com/home/socity/position?postId=F3572C5AD0A1263CE644BEF27E2AA66F)。
- 萤石杭州 AI 测试开发岗位把 ReAct/Function Calling、MCP、Python/FastAPI、CI/CD 和大模型/语音评测放在同一能力组合中：[萤石 AI 测试开发岗位](https://talent.hikvision.com/society/position?postId=9B66DA4515FF1D71D45E16FAF5C92BFA)。
- 零跑杭州 2026 校招 Agent 岗列出工具调用链、记忆、RAG、混合检索、Reranking、日志、Token/延时指标、自动评测、红队测试、Docker 和 Linux；该公开页为职位聚合页，学历要求偏高：[零跑 AI Agent 校招岗位](https://www.shushuqiuzhi.com/position/507910)。
- 杭州校招 Agent 应用岗位样本要求 LangChain/LangGraph、RAG、Agent 评测，并把 Ollama、FastAPI、Docker、Git 和 NLP 项目列为加分项；页面显示职位已截止，只用于技能趋势对照：[Agent 应用开发校招岗位](https://www.shushuqiuzhi.com/position/212884)。

## 2. 当前项目匹配度

| 能力项 | 当前证据 | 当前判定 | 缺口 |
|---|---|---|---|
| Python / FastAPI / WebSocket | 现有本地服务、严格协议、流式会话、435 个后端测试基线 | 强 | 需要统一 CI 与公开复现入口 |
| 本地 LLM 接入 | Ollama/Qwen3 4B 流式推理、模型身份和资源基准 | 强 | 缺原生 Tool Calling |
| 多轮、记忆、知识库 | SQLite 持久化、长期记忆、文档导入、BGE 向量检索和引用 | 中上 | 缺 BM25、融合、Reranker 和质量评测 |
| Agent 工作流 | 只有 ConversationOrchestrator，没有 Agent 状态图 | 弱 | 缺 LangGraph、工具规划、暂停恢复和循环上限 |
| 工具执行安全 | 已有 Session Token、路径/数据边界基础 | 弱 | 缺 Tool Schema、L0/L1/L2、确认、防重放和审计 |
| MCP | 无 | 缺失 | 缺 Server、Client、Schema 适配、超时和安全边界 |
| Agent/RAG 评测 | 有 LLM/ASR/TTS 基准和大量单测 | 中 | 缺固定任务集、Recall/MRR、工具准确率和红队负例 |
| 可观测与交付 | 有诊断脚本和机器基准 | 中下 | 缺 JSON 日志、Metrics、CI、覆盖率门槛和 Docker Demo |
| 前端与产品闭环 | React/TypeScript，文字、语音、人格、记忆和知识界面 | 强 | 当前仍有 36 个前端测试失败 |
| 差异化 | 本地优先、实时语音、插话、隐私边界 | 强 | Kokoro 首段延迟接近 4 秒，需如实披露和验收 |

结论：**当前项目已经超过普通“套 API 聊天框”的水平，适合证明 AI 应用、实时系统和本地部署能力；但还不足以单独支撑“Agent 工程师”这个标题。** 最大短板不是再加一个模型，而是把 Agent 工具闭环、Hybrid RAG、评测和工程交付做成可运行证据。

## 3. 本路线如何补齐岗位信号

| 招聘信号 | 对应实施计划 | 最终证据 |
|---|---|---|
| 代码质量、Git、可复现 | JR-01 | 全绿测试、一键验证、干净 README |
| LangGraph / Function Calling | JR-02 | 有界状态图、Qwen Tool Calling、60 条任务集 |
| 工具安全与业务闭环 | JR-02 | 六个真实工具、确认卡、防重放、审计 |
| MCP | JR-03 | 本地 stdio Server/Client、统一 provider、安全攻击矩阵 |
| RAG / 混合检索 / Reranking | JR-04 | FTS5+向量+RRF+ONNX Reranker、50 条金标集 |
| 评测、Badcase 和性能 | JR-02、JR-04、JR-06 | 工具准确率、Schema 合法率、Recall@5、MRR、引用、p95 |
| Logs / Metrics / CI/CD | JR-05 | 脱敏 JSONL、Prometheus、Windows CI、85% 安全覆盖率 |
| Docker 与文档交付 | JR-05 | 无模型 Web/API Demo、架构图、安全/运维手册 |
| 真机稳定性 | JR-06 | 20 次插话、30 分钟 soak、RAM/VRAM/延迟报告 |

完成六个计划后，这个项目对初级岗位的“项目证据覆盖度”可从当前约 **60/100** 提升到约 **85/100**。这个数字只衡量作品覆盖招聘技能的程度，不是录用概率。

## 4. 项目之外仍需个人具备

项目无法替代以下准备：

- Python 基础：类型、异步、异常、迭代器、上下文管理器、测试和性能定位。
- 计算机基础：数据结构、网络、HTTP/WebSocket、进程/线程、数据库事务与索引。
- LLM 基础：Transformer、Token、上下文窗口、温度、Embedding、幻觉和 Prompt Injection。
- RAG 原理：Chunk 取舍、召回与排序、Recall/MRR、Reranker、引用和拒答。
- Agent 原理：Function Calling、ReAct、状态机、幂等、重试、超时、人工确认和评测。
- Linux、Docker、Git 和基础 Shell：即使主项目是 Windows，本地应用岗位也常考部署基本功。
- 算法题与英文文档阅读：初级/校招筛选仍可能单独考察。
- 业务表达：能用三分钟说清问题、用户、架构、难点、指标、失败案例和个人贡献。
- 学历/专业门槛：部分杭州校招 Agent 岗要求硕士，项目只能增强技术证明，不能消除硬筛条件；应同时投递写明本科可投、0–3 年或重项目经历的 AI 应用/后端岗位。

## 5. 投递判定

- **现在即可投：** AI 应用开发实习/初级、Python AI 后端、语音 AI 应用岗位；简历主打 FastAPI/WebSocket、本地模型、语音闭环、记忆/RAG 和测试规模。
- **完成 JR-01～JR-04 后重点投：** 初级大模型应用工程师、Agent 应用工程师、知识库/RAG 工程师。
- **完成 JR-05～JR-06 后作为主项目投：** 要求 Agent+MCP+评测+工程交付的岗位，并用真实报告而不是功能列表证明能力。
- **暂不作为主目标：** 要求论文、2–3 年生产经验、训练/微调、CUDA 内核、分布式推理或多 Agent 平台经验的中高级/算法岗位。

详细执行顺序见 [`../superpowers/plans/2026-09-09-junior-agent-job-readiness-roadmap.md`](../superpowers/plans/2026-09-09-junior-agent-job-readiness-roadmap.md)。
