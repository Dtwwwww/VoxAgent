# Plan 2：Web 语音对话运行手册

Plan 2 提供同一会话内的文字与语音对话。文字输入只返回文字；麦克风输入会经过本地 VAD、语音识别、Ollama 和语音合成后返回文字与语音。所有服务只监听本机回环地址。

## 当前配置

- Web 前端：`http://127.0.0.1:5173`
- 后端：`127.0.0.1:8765`
- Ollama：`127.0.0.1:11434`
- 本地模型：`qwen3:4b-instruct-2507-q4_K_M`
- 公开音色：`default_voice`（声灵快速音色，Melo）与 `original_voice`（声灵原声音色，Kokoro）
- 已选音色样本：`voice-005`
- 停顿策略：普通句尾约 1.35 秒；“嗯、然后、因为、但是”等未完成句尾约 2 秒

## 手动启动

打开三个 PowerShell 窗口，所有命令都在本项目工作树根目录执行。

窗口 1：启动 Ollama。若 11434 已经监听，可跳过 `Start-Process`。

```powershell
$env:OLLAMA_MODELS = 'D:\VoxAgentData\models\ollama'
$env:OLLAMA_NO_CLOUD = '1'
$env:OLLAMA_HOST = '127.0.0.1:11434'

if (-not (Test-NetConnection 127.0.0.1 -Port 11434 -InformationLevel Quiet)) {
    Start-Process -FilePath 'D:\VoxAgentData\runtime\ollama\ollama.exe' `
      -ArgumentList 'serve' -WindowStyle Hidden
}
```

生成一次临时 token，并复制输出值。token 只用于本次启动，不要提交到 Git，也不要放入 `localStorage`。

```powershell
$token = [Convert]::ToBase64String(
    [Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
).TrimEnd('=').Replace('+','-').Replace('/','_')
$token
```

窗口 2：把上一步输出粘贴给 `$token`，启动后端。第一次连接后，本地语音模型初始化约需十几秒；网页会显示初始化状态。

```powershell
$token = '粘贴刚才生成的 token'
$env:VOXAGENT_DATA_ROOT = 'D:\VoxAgentData'
Set-Location '.\backend'
& '.\.venv\Scripts\voxagent.exe' serve --session-token $token --port 8765
```

窗口 3：启动 Web 前端。

```powershell
Set-Location '.\frontend'
& '.\node_modules\.bin\vite.cmd' --host 127.0.0.1 --port 5173
```

浏览器打开下面地址，并把末尾替换为同一个 token：

```text
http://127.0.0.1:5173/?token=粘贴刚才生成的token
```

## 使用方式

- 在底部输入框提交文字：Agent 只返回文字，不自动朗读。
- 点击麦克风开始说话，再次点击结束：Agent 返回识别文字、回答文字和语音。
- Agent 消息上的朗读按钮可重复朗读；正在朗读时可停止。
- 音色面板可试听快速/原声音色，并选择 `0.8×`、`1.0×` 或 `1.2×` 语速。
- 点击朗读后，底部状态栏会先显示“正在生成朗读…”，收到音频后显示“正在朗读”。
- 点击复制后会显示“已复制”；浏览器拒绝复制时会显示“复制失败”。
- 用户消息显示在右侧并标记“用户”；Agent 消息显示在左侧并标记“Agent（声灵）”。

## 停止

在后端和 Vite 的两个前台 PowerShell 窗口中分别按 `Ctrl+C`。不要结束 GPT、Codex、Roxi、NVIDIA 服务或其他无关进程。Ollama 可继续留在后台供 Plan 1 使用。

## 故障排查

- “token 验证失败”：确认后端的 `--session-token` 与网页 URL 中的 token 完全相同。
- “已有一次会话”：关闭其他 VoxAgent 页面后刷新。
- “无法连接语音服务”：确认 8765 端口正在监听，并查看后端窗口的错误。
- 麦克风无输入：在浏览器地址栏的站点权限中允许麦克风，并确认 Windows 输入设备不是静音状态。
- 首次连接停在初始化：通常需等待十几秒；若随后显示初始化失败，请重启后端并检查 `D:\VoxAgentData\models\speech`。

`/healthz` 不包含 token、模型路径或私有音色编号。运行时模型、音频和会话历史不会提交到 GitHub。
