# JR-03 MCP 互操作 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 MCP Python SDK 2.2 把声灵的受控工具暴露为本地 stdio Server，并让 Agent 可通过同一 Tool Registry 调用 MCP Provider，同时保持权限、确认和审计边界不变。

**Architecture:** `McpToolProvider` 负责启动和关闭一个固定命令的本地子进程，发现工具后将 MCP Schema 转为现有 `ToolDefinition`。模型只看到业务参数；L1/L2 的一次性 capability 由宿主在用户确认后注入，MCP Server 验证 HMAC、参数哈希、过期时间和 nonce，再调用与原生工具相同的业务服务。运行时只能选择 `native` 或 `mcp` provider，避免重复注册同名工具。

**Tech Stack:** Python 3.12、MCP Python SDK 2.2、Pydantic 2、SQLite、HMAC-SHA256、pytest/pytest-asyncio

## Global Constraints

- 只支持本地 `stdio`，本阶段不开放 Streamable HTTP、SSE 或公网端口。
- 子进程命令固定为当前 Python 解释器和 `-m voxagent.mcp.server`，模型与用户输入不能改变命令、参数、工作目录或环境变量。
- 子进程环境只追加 `VOXAGENT_DATA_ROOT`、`VOXAGENT_MCP_CAPABILITY_KEY` 和 `LANGGRAPH_STRICT_MSGPACK`。
- `ToolAnnotations` 只用于描述，不能代替声灵自己的权限检查。
- L1/L2 没有有效 capability 时，MCP Server 的业务执行器调用次数必须为 0。
- capability 有效期 30 秒、只用一次，并绑定 request ID、工具名和规范参数 SHA-256。
- 单次 MCP 调用超时 5 秒，单个结构化结果序列化后最多 64 KiB。
- 每个任务独立提交。

---

### Task 1: 固定 SDK 版本和 MCP 领域契约

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock`
- Create: `backend/src/voxagent/mcp/__init__.py`
- Create: `backend/src/voxagent/mcp/models.py`
- Test: `backend/tests/mcp/test_sdk_contract.py`

**Interfaces:**
- `McpProviderConfig(command, args, cwd, environment, timeout_seconds, maximum_result_bytes)`
- `McpDiscoveredTool(name, description, input_schema, permission)`
- Runtime provider setting: `VOXAGENT_TOOL_PROVIDER=native|mcp`

- [ ] **Step 1: 写 SDK 合同测试**

```python
from mcp import Client, StdioServerParameters
from mcp.server import MCPServer


def test_mcp_v2_surface_is_available() -> None:
    assert Client is not None
    assert StdioServerParameters is not None
    assert MCPServer is not None
    assert callable(MCPServer.run)
```

- [ ] **Step 2: 固定依赖并运行失败测试**

在 `dependencies` 增加：

```toml
"mcp>=2.2,<2.3",
```

Run: `cd backend; uv lock; uv run pytest tests/mcp/test_sdk_contract.py -v`

Expected: lock 文件更新且测试 PASS；若解析到 1.x，停止实现，不为旧 `FastMCP` API 写兼容层。

- [ ] **Step 3: 定义配置边界**

`McpProviderConfig.local_default()` 必须使用 `sys.executable`、参数 `("-m", "voxagent.mcp.server")`、仓库的 `backend` 目录、5 秒超时和 `65_536` 字节上限。`VOXAGENT_TOOL_PROVIDER` 只接受 `native` 或 `mcp`，非法值在启动时失败。

- [ ] **Step 4: 提交**

```powershell
git add backend/pyproject.toml backend/uv.lock backend/src/voxagent/mcp backend/tests/mcp/test_sdk_contract.py
git commit -m "build: add MCP SDK v2 contract"
```

### Task 2: 实现一次性 MCP capability

**Files:**
- Modify: `backend/src/voxagent/db/migrations.py`
- Create: `backend/src/voxagent/mcp/capability.py`
- Test: `backend/tests/db/test_migrations.py`
- Test: `backend/tests/mcp/test_capability.py`

**Interfaces:**
- Schema version: `5`
- `CapabilityIssuer.issue(tool_request_id, tool_name, arguments, now_utc) -> str`
- `CapabilityVerifier.consume(token, tool_name, arguments, now_utc) -> str`

- [ ] **Step 1: 写迁移和安全负例**

迁移测试要求从 0、3、4 三种版本升级到 5，并存在唯一 nonce 表。capability 测试覆盖签名错误、过期、工具名变化、参数变化、格式错误和重放；所有失败都在业务执行器之前发生。

- [ ] **Step 2: 增加 nonce 表**

```sql
CREATE TABLE mcp_capability_nonces (
    nonce TEXT PRIMARY KEY,
    tool_request_id INTEGER NOT NULL REFERENCES tool_requests(id) ON DELETE CASCADE,
    expires_at_utc TEXT NOT NULL,
    consumed_at_utc TEXT NOT NULL
);
CREATE INDEX idx_mcp_capability_expiry
ON mcp_capability_nonces(expires_at_utc);
```

把 `LATEST_SCHEMA_VERSION` 更新为 `5`。迁移必须仍由现有 `BEGIN IMMEDIATE` 包裹。

- [ ] **Step 3: 实现可验证 token**

payload 使用规范 JSON，固定字段为 `tool_request_id`、`tool_name`、`arguments_sha256`、`nonce` 和 `expires_at_utc`。token 格式为：

```text
base64url(payload_without_padding).base64url(hmac_sha256_without_padding)
```

验证顺序固定为：限制 token 长度 2,048 字符、拆分两段、base64 解码、恒定时间校验 HMAC、解析严格字段、比较工具名和参数哈希、检查 30 秒期限、查询 request 已被用户批准、最后用 `BEGIN IMMEDIATE` 插入 nonce。nonce 主键冲突即重放。

- [ ] **Step 4: 密钥生命周期测试**

密钥使用 `secrets.token_bytes(32)` 在主进程启动时生成，只通过显式子进程环境传递。日志、异常、审计和 API 响应不得包含 token 或密钥。重启后旧 capability 必须失效。

- [ ] **Step 5: 运行并提交**

Run: `cd backend; uv run pytest tests/db/test_migrations.py tests/mcp/test_capability.py -v`

Expected: PASS，包括并发双消费时恰好一个成功。

```powershell
git add backend/src/voxagent/db/migrations.py backend/src/voxagent/mcp/capability.py backend/tests/db/test_migrations.py backend/tests/mcp/test_capability.py
git commit -m "security: bind MCP writes to one-time capabilities"
```

### Task 3: 构建本地 MCP Server

**Files:**
- Create: `backend/src/voxagent/mcp/server.py`
- Create: `backend/src/voxagent/mcp/server_tools.py`
- Test: `backend/tests/mcp/test_server.py`

**Interfaces:**
- `build_mcp_server(services: ToolServices, verifier: CapabilityVerifier) -> MCPServer`
- Server name: `VoxAgent Local Tools`
- Tools: `knowledge.search`, `reminders.list`, `reminders.create`, `reminders.complete`

- [ ] **Step 1: 写 in-process 工具发现测试**

```python
async def test_server_lists_only_approved_tools(mcp_server) -> None:
    async with Client(mcp_server, raise_exceptions=True) as client:
        result = await client.list_tools()
    assert {tool.name for tool in result.tools} == {
        "knowledge.search",
        "reminders.list",
        "reminders.create",
        "reminders.complete",
    }
```

- [ ] **Step 2: 用 v2 API 注册工具**

创建 `MCPServer("VoxAgent Local Tools")`，分别用
`@server.tool(name="knowledge.search")`、`@server.tool(name="reminders.list")`、
`@server.tool(name="reminders.create")` 和
`@server.tool(name="reminders.complete")` 注册四个闭包。查询参数沿用 JR-02 的
Pydantic 约束；创建/完成额外接收 `_voxagent_capability: str`，并在调用业务服务之前执行
`verifier.consume()`。只读工具设置 `read_only_hint=True`，写工具设置
`read_only_hint=False`；所有工具设置 `open_world_hint=False`。

- [ ] **Step 3: 结构化返回与错误边界**

工具返回 Pydantic 结果模型，使 MCP 同时产生 text content 和 `structured_content`。业务失败只返回稳定错误码和短消息；SQLite 路径、堆栈、密钥、capability 与绝对路径写到任何返回内容都视为测试失败。

- [ ] **Step 4: 添加安全调用测试**

测试 L0 无 capability 正常工作；L2 缺少、伪造、过期和重放 token 均失败；有效 token 恰好执行一次并产生现有 Tool Audit。测试通过 `Client(mcp_server, raise_exceptions=True)` 走真实 MCP 协议层。

- [ ] **Step 5: 增加 stdio 入口**

`main()` 从 `VOXAGENT_DATA_ROOT` 和 `VOXAGENT_MCP_CAPABILITY_KEY` 建立依赖；缺少任一值时向 stderr 输出稳定错误并返回 2。模块底部仅包含：

```python
if __name__ == "__main__":
    raise SystemExit(main())
```

`main()` 内调用 `server.run()`，默认 transport 即 stdio。模块导入时不得打开数据库、打印 stdout 或启动服务。

- [ ] **Step 6: 运行并提交**

Run: `cd backend; uv run pytest tests/mcp/test_server.py -v`

Expected: PASS；stdout 捕获中没有非 MCP 文本。

```powershell
git add backend/src/voxagent/mcp/server.py backend/src/voxagent/mcp/server_tools.py backend/tests/mcp/test_server.py
git commit -m "feat: expose guarded tools over local MCP"
```

### Task 4: 实现受限 stdio Client 生命周期

**Files:**
- Create: `backend/src/voxagent/mcp/client.py`
- Test: `backend/tests/mcp/test_client.py`

**Interfaces:**
- `McpLocalClient.start() -> tuple[McpDiscoveredTool, ...]`
- `McpLocalClient.call(name, arguments) -> dict[str, object]`
- `McpLocalClient.close() -> None`

- [ ] **Step 1: 写生命周期测试**

覆盖：启动一次、分页发现全部工具、调用成功、5 秒超时、结果超过 64 KiB、未知 content block、server 异常、close 后调用和重复 close。测试使用可注入的 in-process Client factory；另写一个真实 stdio smoke test。

- [ ] **Step 2: 构造固定启动参数**

```python
parameters = StdioServerParameters(
    command=sys.executable,
    args=["-m", "voxagent.mcp.server"],
    env={
        "VOXAGENT_DATA_ROOT": str(data_root),
        "VOXAGENT_MCP_CAPABILITY_KEY": capability_key,
        "LANGGRAPH_STRICT_MSGPACK": "true",
    },
    cwd=backend_root,
    encoding="utf-8",
    encoding_error_handler="strict",
)
```

生产路径使用 `Client(parameters, read_timeout_seconds=5.0)`，由一个 `async with` 覆盖整个应用生命周期；不得每次工具调用启动新子进程。

- [ ] **Step 3: 严格发现和返回解析**

以 `cursor=None` 开始，循环 `list_tools(cursor=cursor)` 并把返回的 `next_cursor`
赋给 cursor，直到其为 `None`；限制最多 10 页、100 个工具。只接受批准名称集合，
拒绝重复名和未知名。调用后要求 `is_error is False` 且 `structured_content` 为对象；
用紧凑 JSON 计算 64 KiB 上限。只有 L0 调用在进程启动失败、连接关闭或超时这三类
瞬时错误下允许重建 client 后重试一次；L1/L2 和服务端返回的业务错误永不重试。

- [ ] **Step 4: 真实 stdio smoke test**

用临时数据根和 32 字节测试密钥启动子进程，发现四个工具并调用 `reminders.list`。测试标记为普通集成测试，不依赖 Ollama、GPU、麦克风或模型文件。

- [ ] **Step 5: 运行并提交**

Run: `cd backend; uv run pytest tests/mcp/test_client.py -v`

Expected: PASS；测试结束后不存在遗留 `voxagent.mcp.server` 子进程。

```powershell
git add backend/src/voxagent/mcp/client.py backend/tests/mcp/test_client.py
git commit -m "feat: manage local MCP client lifecycle"
```

### Task 5: 适配统一 Tool Registry

**Files:**
- Create: `backend/src/voxagent/mcp/provider.py`
- Modify: `backend/src/voxagent/tools/registry.py`
- Modify: `backend/src/voxagent/agent/service.py`
- Modify: `backend/src/voxagent/cli.py`
- Test: `backend/tests/mcp/test_provider.py`
- Modify: `backend/tests/agent/test_service.py`

**Interfaces:**
- `McpToolProvider.definitions() -> tuple[ToolDefinition, ...]`
- `McpToolProvider.execute(call, authorization) -> ToolResult`
- CLI option: `voxagent serve --tool-provider native|mcp`

- [ ] **Step 1: 写 provider 一致性测试**

同一个参数输入下，native 与 MCP provider 的工具名、权限、业务字段和稳定错误码必须一致。MCP 输入 Schema 中的 `_voxagent_capability` 在转换成 `ToolDefinition` 前删除，因此该字段不能出现在传给 Ollama 的 Schema。

- [ ] **Step 2: 注册远程定义**

当配置为 `mcp` 时，只注册 MCP 暴露的四个工具；`files.search_authorized` 和 `apps.open_allowlisted` 继续由 native provider 注册。注册表最终仍恰好包含 JR-02 的六个工具且无重名。

- [ ] **Step 3: 注入写能力**

`execute()` 收到已批准的 L1/L2 `AuthorizationDecision` 后签发 capability，并只在 MCP wire arguments 中增加 `_voxagent_capability`。L0 不签发；deny、expired、cancelled 状态不调用 client。MCP 返回仍写入同一个 `tool_requests/tool_audit` 生命周期。

- [ ] **Step 4: 接入启动和关闭**

`serve --tool-provider` 默认 `native`；选择 `mcp` 时在 FastAPI lifespan 中启动 client，启动失败则整个应用失败，不静默退回 native。shutdown 必须先停止新 Agent turn、等待最多 3 秒在途 MCP 调用，再关闭 client 和数据库。

- [ ] **Step 5: 运行并提交**

Run: `cd backend; uv run pytest tests/mcp/test_provider.py tests/agent/test_service.py -v`

Expected: PASS；native/mcp 两种模式的未授权执行数均为 0。

```powershell
git add backend/src/voxagent/mcp/provider.py backend/src/voxagent/tools/registry.py backend/src/voxagent/agent/service.py backend/src/voxagent/cli.py backend/tests/mcp/test_provider.py backend/tests/agent/test_service.py
git commit -m "feat: route agent tools through MCP provider"
```

### Task 6: 增加 MCP 可复现演示与边界测试

**Files:**
- Create: `scripts/demo-mcp.ps1`
- Create: `backend/tests/security/test_mcp_boundaries.py`
- Modify: `docs/portfolio/demo-script.md`
- Modify: `README.md`

**Interfaces:**
- Demo: `powershell -ExecutionPolicy Bypass -File scripts/demo-mcp.ps1`
- Exit codes: `0` success、`1` assertion failure、`2` environment/configuration failure

- [ ] **Step 1: 写攻击矩阵**

覆盖直接调用写工具、猜测 capability、改参数、改工具名、重放、并发重放、过期、未知工具、64 KiB 输出和 stdio stdout 污染。每个案例断言业务服务调用次数、request 状态和 audit 状态。

- [ ] **Step 2: 实现确定性演示脚本**

脚本建立临时数据根，迁移数据库，启动 MCP client，打印发现的四个工具，调用只读提醒列表，再证明无 capability 的创建被拒绝。脚本不得打开 GUI、下载模型或修改真实 `D:\VoxAgentData`。

- [ ] **Step 3: 更新文档**

README 增加 stdio 架构图、native/mcp 切换命令和安全说明。演示脚本新增一段：展示 Tool Schema、执行 L0 查询、批准一次 L2 创建、展示审计，再重放同一请求并证明失败。

- [ ] **Step 4: 完整验证并提交**

Run: `powershell -ExecutionPolicy Bypass -File scripts/demo-mcp.ps1`

Expected: 显示 `MCP demo passed`，exit 0。

Run: `powershell -ExecutionPolicy Bypass -File scripts/verify.ps1 -Scope All`

Expected: `Verification passed.`，exit 0。

```powershell
git add scripts/demo-mcp.ps1 backend/tests/security/test_mcp_boundaries.py docs/portfolio/demo-script.md README.md
git commit -m "test: document and verify MCP security boundary"
```

## JR-03 Completion Gate

- SDK 合同证明使用 MCP Python SDK 2.2 的 `MCPServer` 和 `Client` API。
- `scripts/demo-mcp.ps1` 可在无模型、无 GPU、无麦克风环境运行。
- Agent 可选择 MCP provider，并保持最终六个工具名和权限不变。
- L1/L2 缺少、伪造、改参、过期和重放 capability 的实际执行数均为 0。
- stdio 子进程只接收最小环境，命令和工作目录不可由模型控制。
- MCP 超时、大小上限、未知工具、分页和生命周期测试通过。
- `scripts/verify.ps1 -Scope All` 通过。
