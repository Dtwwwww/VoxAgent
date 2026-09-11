# VoxAgent 当前能力与证据边界

**基线：** JR-02 安全 Agent MVP（2026-09-11）
**目标岗位：** 初级大模型应用工程师 / Agent 应用工程师 / 语音 AI 应用工程师

本文只描述当前仓库可运行、可测试或已有实测报告的能力。路线图中的 MCP、Hybrid RAG、可观测性和交付增强不计入当前能力。

## 能力矩阵

| 领域 | 已实现 | 可核查证据 | 当前边界 |
| --- | --- | --- | --- |
| Python 服务 | FastAPI HTTP / WebSocket、本机回环监听、启动期 Session Token、严格请求与事件模型 | `backend/src/voxagent/api/`、`contracts/protocol-fixtures.json`、后端 API / e2e 测试 | 不是公网多租户服务；没有 OAuth、RBAC 或远程部署 |
| 流式对话 | Ollama 流式生成、文字/语音统一消息流、轮次和请求 ID 关联、取消与旧事件隔离 | `backend/src/voxagent/conversation/`、`frontend/src/useVoiceSession.ts` | 工具确认恢复后的回答暂不自动 TTS |
| 安全 Agent | Qwen3 Tool Calling、LangGraph 单 Agent、冻结式六工具注册表、3 次工具/8 节点上限、L0/L1/L2、单次确认、防重放和脱敏审计 | `backend/src/voxagent/agent/`、`backend/src/voxagent/tools/`、`frontend/src/components/ToolApprovalCard.tsx` | 不是多 Agent；无 MCP；真实 Ollama 稳定性与 60 条正式评测尚未验收 |
| 实时语音 | 本地 VAD、流式/最终 ASR、短句 TTS、插话、模式切换、浏览器语音失败后本地回退 | `backend/src/voxagent/speech/`、`frontend/src/realtime/`、`benchmarks/voice-loop-acceptance.json` | 默认仅本地；物理麦克风与扬声器体验仍需人工验收 |
| 长期记忆 | 建议后确认、手动增删改、来源轮次、敏感内容拦截、本地 embedding 检索 | `backend/src/voxagent/memory/`、`frontend/src/memory/` | 不是跨用户记忆服务；策略不能替代完整 DLP |
| 本地知识 | TXT / Markdown / 文本 PDF / DOCX 解析、分段、BGE 向量、去重、事务导入、引用展示 | `backend/src/voxagent/knowledge/`、`frontend/src/knowledge/` | 无 OCR；当前不是 BM25 + Vector + Reranker |
| 本地数据 | SQLite 迁移、导出、确认式完整清空、每日备份与完整性检查 | `backend/src/voxagent/db/`、`backend/src/voxagent/api/data.py` | 删除索引不会删除用户原始文档 |
| 模型与硬件 | 锁定模型清单、SHA-256 身份、LLM / ASR / TTS 延迟与资源探针、稳定性失败记录 | `benchmarks/target-machine-baseline.json`、`benchmarks/README.md` | 报告绑定特定目标机器，不代表其他硬件性能 |
| 工程质量 | pytest、Ruff、Vitest、TypeScript、Vite 构建统一验证；覆盖并发取消、协议、数据与 UI | `scripts/verify.ps1`、`backend/tests/`、`frontend/src/**/*.test.*` | Windows PowerShell 是当前首要复现环境；CI 尚未接入 |

## 已落实的关键契约

1. 首次加载和旧配置迁移后都使用 `local-only`，在线语音只能显式选择。
2. Token 只用于当前启动和请求认证，不进入浏览器持久化、数据库、报告或 Git。
3. 文字输入的模型回复保持静默；手动朗读与实时自动朗读拥有独立的取消边界。
4. 语音、TTS 和 WebSocket 事件按 session、turn、request 与 sequence 关联，迟到事件不能复活旧输出。
5. 文档和记忆只作为不可信参考上下文；人格或知识内容不能覆盖固定安全边界。
6. 导入失败或取消不留下半份知识索引，完整清空需要明确确认短语。
7. L1/L2 工具未确认时执行次数为零；确认票据绑定会话、轮次和 SHA-256 参数摘要，120 秒过期且不可重放。
8. 前端确认只回传 `confirmation_id`，不接收、不显示也不能修改工具参数；审计界面只渲染允许公开的机器字段。

## 当前不应宣称的能力

- 不应写“已完成生产级 Agent 平台”：当前是可演示的单 Agent MVP，12 条 fake 冒烟评测不能替代 60 条真实模型评测与稳定性验收。
- 不应写“已支持 MCP”：当前没有 MCP Server / Client 或 provider 适配层。
- 不应写“已实现 Hybrid RAG”：当前为本地向量检索，没有 FTS5、RRF、Reranker 和固定金标评测。
- 不应写“生产级可观测与部署”：当前没有结构化追踪、Prometheus、CI/CD、容器化演示和生产 SLO。
- 不应写“所有真机语音验收已通过”：自动回环已通过，但真实说话、听感和批量插话仍待人工。

## 复现入口

完整自动化 gate：

```powershell
& .\scripts\verify.ps1 -Scope All
```

紧凑 Agent 冒烟评测：

```powershell
Set-Location .\backend
uv run --extra dev voxagent evaluate-agent --dataset ..\benchmarks\agent-mvp-dataset.json --mode fake
```

应用启动与数据操作分别见：

- `docs/plan-01/README.md`：模型、运行目录和硬件基线。
- `docs/plan-02/README.md`：文字 / 语音会话与故障排查。
- `docs/plan-03/README.md`：人格、记忆、知识、导出和备份。
- `docs/portfolio/demo-script.md`：面试三分钟演示。

## 面试表达建议

用“问题—取舍—证据”讲项目：低显存 Windows 机器需要本地隐私和连续语音，因此选择 4B 量化模型、回环服务和本地优先语音；用严格关联 ID、取消所有权与事务写入控制并发失败；最后用可重复测试、模型身份和失败的 soak 报告证明结论，而不是只展示成功截图。
