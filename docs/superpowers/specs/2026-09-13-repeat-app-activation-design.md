# 允许列表应用动作可靠性设计

日期：2026-09-13
状态：已按主流实现模式修订，等待用户书面规格复核

## 背景与根因

用户首次要求“打开计算器”时，`apps.open_allowlisted` 产生了 L1 确认票据、执行记录和成功审计。之后明确要求“再帮我打开计算器”时，审计库没有新增 `request.created`：第二轮根本没有进入工具授权或执行链，模型仅依据历史回答“已经打开”。前端又持续展示上一轮工具的“已完成”状态，使未执行的第二轮看起来已经成功。

补充复现进一步证明这不是计算器特例：第 20 轮明确输入“打开记事本”后，审计库仍只有第 7 轮计算器的唯一工具请求，系统中也没有 `Notepad.exe` 进程，但助手保存了“记事本已经打开”的回复。旧的计算器完成状态同时继续显示。根因因此定义为：所有明确应用动作都缺少确定性执行保证和本轮证据约束，而不只是重复启动失败。

当前 `WindowsAllowlistedLauncher` 每次调用都会执行 `calc.exe`，自身没有去重逻辑。因此问题包含三个边界：明确动作请求依赖模型自愿发起工具调用、工具结果会被模型重新表述、前端活动状态没有按对话轮次失效。

本机 Windows 11 的计算器由 `CalculatorApp.exe` 运行，并通过 `ApplicationFrameHost.exe` 暴露顶层窗口。窗口识别不能依赖“计算器”这一中文标题，否则在其他系统语言或标题变化后失效。

## 目标

1. 任何一次明确请求打开允许列表应用时，都必须进入新的工具链；首次请求启动该应用。
2. 应用已有可识别窗口时，后续明确请求恢复并激活现有窗口，不创建第二个窗口。
3. 每个动作请求都生成新的 L1 单次确认、请求记录和审计链，不复用旧票据或旧结果；没有本轮工具证据时不得声称已执行。
4. 界面和助手文本只报告本轮真实结果，不得把上一轮成功冒充为本轮成功。
5. Windows 拒绝后台程序抢占前台时，闪烁对应任务栏窗口并准确说明需要用户点击。
6. 保持现有允许列表，不增加 Shell、PowerShell、任意可执行文件或任意窗口控制能力。

## 非目标

- 不增加关闭、最小化、移动、输入按键或控制应用内容的能力。
- 不允许模型提供进程名、PID、窗口句柄或命令行。
- 不保证绕过 Windows 的前台焦点保护；系统拒绝时不得使用模拟 Alt 键、注入输入或其他规避手段。
- 不为任意第三方应用建立通用启动器配置。
- 不把“帮我计算表达式”解释为启动计算器。

## 方案比较

### 方案 A：仅强化系统提示

要求模型在每次动作请求中重新调用工具。改动小，但 Qwen3 仍可能根据历史直接声称成功，无法提供确定性保证，不能单独作为修复。

### 方案 B：仅增强 Windows Launcher

把 `calc.exe` 的重复启动改为窗口激活。能改善已经进入执行器的调用，但无法解决第二轮没有工具调用和前端旧状态残留，也不能单独作为修复。

### 方案 C：确定性动作路由、状态感知 Launcher 与可信结果闭环

在模型之前识别边界清晰的允许列表应用动作，每次建立新的受控调用；执行器根据真实窗口状态选择启动、聚焦或任务栏提示；应用类副作用工具直接使用可信结果收口，前端只展示当前轮活动。该方案改动集中、可测试，并同时覆盖三个根因，因此采用此方案。

## 主流实现模式与采用决策

- Semantic Kernel 对明确工具操作提供 `FunctionChoiceBehavior.Required`，表明成熟 Agent 框架不会只依靠提示词要求模型调用工具。VoxAgent 当前使用的 Ollama 接口不能可靠强制 `tool_choice=required`，因此在应用层加入范围严格的确定性路由，作为本地模型环境下的等价可靠性边界。
- LangGraph 的中断恢复以持久化状态和同一 `thread_id` 关联待执行动作。VoxAgent 延续现有确认链，并进一步要求每个动作绑定同一 `session_id`、`turn_id`、`call_id` 和一次性 `confirmation_id`；恢复只能消费这一个待执行动作。
- Vercel AI SDK 的工具界面按 `toolCallId` 表达 `approval-requested`、执行结果和错误等生命周期状态。VoxAgent 不再依赖全局最后一条工具活动，也不只通过清空数组掩盖旧状态，而是按当前轮次和调用 ID 选择展示记录。
- Electron 的单实例模式会在第二次启动请求时恢复并聚焦已有窗口；VS Code 在 Windows 上也将后续请求转交给已有实例并显式处理前台权限。VoxAgent 对允许列表应用采用相同的“查找、恢复、聚焦，否则启动”语义。
- Windows 不保证后台程序可以强制获得前台焦点。因此聚焦失败必须退化为 `FlashWindowEx` 请求注意，并报告 `attention_requested`，不能把请求已送达或任务栏已闪烁表述成已经聚焦。
- MCP 将工具视为模型可控能力，但把交互方式留给宿主应用，同时要求清晰展示工具动作并允许用户拒绝。VoxAgent 对 L1 应用动作继续逐次确认，确定性路由不会绕过 Tool Schema、Policy、Confirmation 或审计。

## 行为契约

### 明确动作识别

仅对允许列表中的 `calculator` 和 `notepad` 做确定性识别：

- 当前用户消息同时包含应用名称或无歧义别名，以及“打开”“启动”“切换到”“调出”“回到”等动作词时，直接生成 `apps.open_allowlisted` 调用。
- “再打开计算器”“再帮我打开一下计算器”与首次请求完全相同，每轮都产生新的调用和确认。
- 当前消息只包含“再打开它”“打开”等指代时，仅当最近一条用户消息明确提到唯一一个允许列表应用，且当前消息包含重新激活动作时才解析该指代；不能使用助手历史中的自述作为事实来源。
- 包含明确否定词的消息，例如“不要打开计算器”，不得进入确定性工具路由。
- “用计算器怎么算”“计算器有什么功能”“我已经打开计算器”等非动作表达不得触发工具。
- 无法确定时继续交给模型，但后端仍不得将无工具证据的助手成功声明视为已执行。

确定性路由只生成与用户文本对应的固定 `app_id`；模型不能注入命令、进程或窗口参数。调用随后进入现有 Tool Schema、L1 Policy、Confirmation 和防重放链路。

该路由只负责两个允许列表应用的明确动作，不替代一般意图理解：模糊请求、知识问答和其他工具仍由模型选择。不得通过提高 Prompt 强度、调整温度或重试模型来模拟强制工具调用。

### 每轮独立授权

重复动作不是重放：每个新用户轮次生成新的 `call_id`、工具请求和 `confirmation_id`。旧票据仍受 session、turn、参数哈希、120 秒过期和单次消费约束。即使只是切换已有窗口，也必须重新点击“允许一次”。

拒绝本轮确认不会影响已打开的应用，也不得复用上一轮“已完成”状态。

### Windows 窗口识别与激活

Launcher 使用内部冻结的应用规格，不接受模型提供的任何系统标识：

- `calculator`：启动命令为 `calc.exe`，目标进程为 `CalculatorApp.exe`；允许顶层窗口由 `ApplicationFrameHost.exe` 承载，但必须能在其子窗口中关联到目标进程。
- `notepad`：启动命令为 `notepad.exe`，目标进程为 `Notepad.exe`；使用相同的顶层窗口枚举接口。

识别过程枚举可见顶层窗口，并通过窗口自身或后代窗口的 PID 映射到冻结的目标进程名。不得依赖窗口标题、系统语言或任意正则配置。存在多个匹配窗口时，优先选择当前前台窗口；否则选择最近发现的可见窗口，且只激活一个。

激活流程：

1. 若窗口最小化，调用 `ShowWindow(..., SW_RESTORE)`。
2. 调用 `SetForegroundWindow` 并读取返回值。
3. 成功时返回 `focused`。
4. Windows 拒绝前台激活时调用 `FlashWindowEx` 请求任务栏注意，并返回 `attention_requested`，不得报告已聚焦。
5. 未找到目标进程关联窗口时，通过固定命令启动应用并返回 `launched`。
6. 若检测到目标进程但窗口可能仍在创建，做短时有界轮询；超时后返回安全失败，不再启动第二个实例。

内部窗口接口使用标准库 `ctypes` 封装 Win32 API，不增加运行时依赖。非 Windows 平台继续明确失败；自动测试全部注入 Fake Window API 和 Fake Process Launcher。

### 可信结果与回复

Launcher 返回结构化结果：

- `launched`：`已启动计算器。`
- `focused`：`已切换到计算器。`
- `attention_requested`：`计算器已在任务栏提示，请点击切换。`

结果只包含 `app_id` 和上述固定 outcome，不包含 PID、窗口句柄、路径或命令。审计的脱敏 detail 增加 outcome，保留 duration；前端公共工具事件继续只接收固定摘要。

`apps.open_allowlisted` 成功或安全失败后不再调用模型重新解释结果。编排器使用工具产生的可信摘要完成该轮，避免模型声称不存在的窗口动作。知识搜索等需要模型整合结果的工具保持原流程。

系统执行以下不可绕过的不变量：只有当前 `session_id + turn_id + call_id` 存在匹配的真实工具终态，才允许产生对应动作结论。

- `tool.completed` 且 outcome 为 `launched` 时，才能显示或回复“已启动”。
- `tool.completed` 且 outcome 为 `focused` 时，才能显示或回复“已切换”。
- `tool.completed` 且 outcome 为 `attention_requested` 时，只能提示用户点击任务栏。
- `tool.failed`、拒绝、超时或缺少匹配终态时，不得出现“已打开”“已启动”“已切换”或“已完成”等成功措辞。
- 模型文本、历史助手消息、进程存在和旧工具事件都不能单独作为成功证据。

### 前端轮次状态

前端把工具活动保存为由 `session_id + turn_id + call_id` 标识的生命周期记录。发送新的文字请求或语音产生新一轮最终文本时，将该轮设为活动轮次；收到 `tool.approval_required`、`tool.started`、`tool.completed` 或 `tool.failed` 时，只更新具有相同标识的记录。若现有协议事件缺少其中任一关联字段，后端必须补齐后才能依赖该事件驱动动作状态。

主对话区只选择当前活动轮次的记录，表达等待确认、执行中、已启动、已切换、需要点击或失败；当前轮没有工具事件时不渲染状态卡。旧轮记录可以留在内存或历史视图，但不能由 `.at(-1)` 等全局最后事件逻辑重新显示。审计面板仍可查看历史记录，不受当前轮选择影响。

工具状态卡具有明确的显示寿命：

- `等待确认` 和 `执行中` 在状态变化前持续显示。
- `已启动`、`已切换` 和其他成功完成状态显示 3 秒后自动消失。
- `需要点击任务栏` 和 `执行失败` 持续显示，直到用户主动关闭或开始下一轮。
- 自动消失计时器必须绑定对应的 `session_id + turn_id + call_id`；计时器到期时只能移除自身记录，不能清除后来产生的新调用状态。
- 新一轮消息开始时立即停止展示上一轮状态，并清理上一轮尚未触发的显示计时器。

## 数据流

1. 用户提交“再帮我打开计算器”。
2. 确定性动作识别器产生新的 `apps.open_allowlisted({app_id: "calculator"})` 调用。
3. 现有策略层创建新的 L1 请求和一次性确认票据。
4. 用户点击“允许一次”。
5. Launcher 枚举允许列表目标窗口：存在则恢复并尝试聚焦，不存在则启动。
6. 工具记录实际 outcome，发出当前轮完成事件。
7. 编排器验证匹配的 `session_id + turn_id + call_id` 终态，并用固定可信摘要结束该轮。
8. 前端按相同标识显示对应当前轮状态；下一轮开始后旧状态不再入选主对话区，但审计记录继续保留。

## 错误处理

- 应用启动命令失败：工具失败并记录固定错误码 `app_launch_failed`。
- 找到进程但在有界等待后仍无窗口：失败码 `app_window_unavailable`，不得再次启动。
- `SetForegroundWindow` 返回失败：不是工具失败，返回 `attention_requested` 并闪烁任务栏。
- 窗口枚举或 Win32 API 异常：失败码 `app_activation_failed`，不泄露操作系统错误详情。
- 用户拒绝或票据失效：沿用现有 confirmation 错误，Launcher 调用次数必须为零。
- Agent 对没有成功工具证据的动作请求输出“已经打开/切换”时，不把该文本作为动作结果；明确动作已由确定性路由覆盖。

## 测试设计

测试保持聚焦，不扩充无关评测：

1. 路由单测：首次打开记事本、首次打开计算器和连续两次明确请求各生成一个新调用；否定、询问与计算表达式不触发；用户历史中的唯一应用可解析“再打开它”。
2. Policy/Confirmation 集成测试：两轮获得不同票据；每轮未确认执行次数为零；旧票据不可用于第二轮。
3. Launcher 单测：无窗口时只启动一次；已有窗口时不启动进程并返回 `focused`；最小化窗口先恢复；系统拒绝焦点时闪烁并返回 `attention_requested`；进程存在但窗口超时时不重复启动。
4. Agent 回归测试：连续两轮允许后 Fake Launcher 收到两次调用，仓库存在两条独立成功请求；应用工具后不再请求模型生成虚假总结；缺少当前调用终态时无法生成成功结论。
5. 前端回归测试：新文字或语音轮次不选择旧工具状态；只有 `session_id + turn_id + call_id` 匹配的事件才能更新当前卡片；缺少本轮事件时不显示“已完成”；成功状态在 3 秒后消失，旧计时器不能移除新调用状态；失败和需要点击状态不会自动消失。
6. Windows 人工冒烟：第一次请求启动计算器，切回浏览器后第二次请求恢复并激活同一个计算器窗口；审计中出现两组不同 request/confirmation。

## 验收标准

- “打开计算器”后允许一次，计算器启动且本轮显示“已启动”。
- 新会话直接输入“打开记事本”时必须出现记事本专属的新确认卡；允许后系统中存在可识别的记事本窗口，本轮审计新增对应请求，页面不得显示之前计算器的完成状态。
- 切回浏览器后输入“再帮我打开计算器”，再次出现新的确认卡；允许后不创建第二个窗口，现有窗口被恢复并尝试切到前台。
- 若 Windows 拒绝抢焦点，计算器任务栏闪烁，界面提示用户点击，不显示“已切换”。
- 两轮审计拥有不同的 request、turn、call 和 confirmation；每张票据只消费一次。
- 第二轮未确认或拒绝时 Launcher 不执行，且页面不沿用第一轮“已完成”。
- 成功状态显示 3 秒后自动消失；等待确认和执行中不会提前消失；失败与需要点击状态保留到用户关闭或下一轮开始。
- 所有新增回归测试、现有后端测试、Ruff、前端测试、TypeScript 和 Vite 构建通过。

## 安全与兼容性

设计不扩大 Agent 权限：仍只有两个固定应用 ID，仍为 L1 单次确认，仍没有任意命令或窗口控制入口。窗口句柄与进程信息仅在 Launcher 内部短暂使用，不写入协议、模型上下文或公开审计。

Windows 可能基于前台锁定规则拒绝后台进程抢占焦点。实现尊重返回值并退化为任务栏提示，不采用输入模拟或焦点保护绕过。该行为与 Microsoft 对 `SetForegroundWindow` 的平台约束一致。

参考：[Microsoft Learn — SetForegroundWindow function](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow)

## 参考实现与规范

- [Microsoft Semantic Kernel — FunctionChoiceBehavior](https://learn.microsoft.com/eu-es/dotnet/api/microsoft.semantickernel.functionchoicebehavior?view=semantic-kernel-dotnet)
- [Ollama — OpenAI compatibility](https://github.com/ollama/ollama/blob/main/docs/api/openai-compatibility.mdx)
- [LangGraph — Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [Vercel AI SDK — Chatbot tool usage](https://ai-sdk.dev/docs/ai-sdk-ui/chatbot-tool-usage)
- [Model Context Protocol — Tools](https://modelcontextprotocol.io/specification/draft/server/tools)
- [Electron — app single-instance behavior](https://www.electronjs.org/docs/latest/api/app)
- [VS Code — Windows existing-instance handoff](https://github.com/microsoft/vscode/blob/main/src/vs/code/electron-main/main.ts)
- [Microsoft Learn — FlashWindowEx function](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-flashwindowex)
