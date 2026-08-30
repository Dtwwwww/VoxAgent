# Plan 1：本地模型运行手册

本文档适用于已完成 Plan 1 部署的目标电脑。当前可使用本地文字模型；Web 语音对话将在 Plan 2 实现。

## 当前部署

- Ollama：`D:\VoxAgentData\runtime\ollama\ollama.exe`
- 模型目录：`D:\VoxAgentData\models\ollama`
- 本地服务：`127.0.0.1:11434`
- 默认模型：`qwen3:4b-instruct-2507-q4_K_M`
- 离线设置：`OLLAMA_NO_CLOUD=1`

已下载模型：

- `qwen3:4b-instruct-2507-q4_K_M`：默认稳定模型
- `qwen3.5:4b`：质量候选，长时间运行时内存压力较高
- `qwen3:1.7b`：低内存紧急备用，语义质量较弱

## 手动启动并开始对话

打开 Windows PowerShell，执行：

```powershell
$env:OLLAMA_MODELS = 'D:\VoxAgentData\models\ollama'
$env:OLLAMA_NO_CLOUD = '1'
$env:OLLAMA_HOST = '127.0.0.1:11434'

$ollama = 'D:\VoxAgentData\runtime\ollama\ollama.exe'

if (-not (Test-NetConnection 127.0.0.1 -Port 11434 -InformationLevel Quiet)) {
    Start-Process -FilePath $ollama -ArgumentList 'serve' -WindowStyle Hidden
    Start-Sleep -Seconds 3
}

& $ollama run 'qwen3:4b-instruct-2507-q4_K_M'
```

进入对话后直接输入中文。输入 `/bye` 退出对话。

如果 Ollama 已在后台运行，只需执行：

```powershell
& 'D:\VoxAgentData\runtime\ollama\ollama.exe' run 'qwen3:4b-instruct-2507-q4_K_M'
```

## 查看已经下载的模型

```powershell
& 'D:\VoxAgentData\runtime\ollama\ollama.exe' list
```

## 查看正在运行的模型

```powershell
& 'D:\VoxAgentData\runtime\ollama\ollama.exe' ps
```

## 查看模型详细信息

```powershell
& 'D:\VoxAgentData\runtime\ollama\ollama.exe' show 'qwen3:4b-instruct-2507-q4_K_M'
```

将最后的模型名称替换为 `qwen3.5:4b` 或 `qwen3:1.7b`，可以查看其他模型。

## 切换模型体验

体验质量候选模型：

```powershell
& 'D:\VoxAgentData\runtime\ollama\ollama.exe' run 'qwen3.5:4b'
```

体验低内存备用模型：

```powershell
& 'D:\VoxAgentData\runtime\ollama\ollama.exe' run 'qwen3:1.7b'
```

同一时间建议只运行一个模型，避免 16GB 内存出现压力。

## 释放模型占用的显存

以下命令只卸载当前模型，不删除模型文件：

```powershell
& 'D:\VoxAgentData\runtime\ollama\ollama.exe' stop 'qwen3:4b-instruct-2507-q4_K_M'
```

## 运行硬件预检

在项目根目录执行：

```powershell
& .\scripts\voxagent_runtime.ps1 -Command {
    Push-Location backend
    uv run voxagent preflight --json
    Pop-Location
}
```

空载检查时，结果中的 `issues` 应为空数组。如果刚使用过模型，可能出现 `ram_low`；先输入
`/bye` 退出对话，再执行下面的命令卸载模型，随后重新运行预检：

```powershell
& 'D:\VoxAgentData\runtime\ollama\ollama.exe' stop 'qwen3:4b-instruct-2507-q4_K_M'
```

## 验证本地离线 Ollama

在项目根目录执行：

```powershell
& .\scripts\voxagent_runtime.ps1 -RequireVerifiedOllama -Command {
    Push-Location backend
    uv run voxagent verify-ollama-runtime `
      --data-root 'D:\VoxAgentData' `
      --baseline '..\benchmarks\target-machine-baseline.json' `
      --json
    Pop-Location
}
```

成功时应显示 `"valid": true`、`"offline_status": "verified"`，并确认服务只监听 `127.0.0.1`。

## 常见说明

- 模型回答“自己在云端”不代表实际调用云端。原始模型无法读取自己的运行环境，可能根据训练语料猜测。
- 当前模型没有文件、屏幕、浏览器、剪贴板或命令行工具权限；它只能看到你输入的对话内容。
- 断网后仍可进行本地文字对话，但不能联网搜索、下载模型或使用 ChatGPT/Codex。
- 模型权重、语音资产、缓存和日志只保存在 `D:\VoxAgentData`，不会提交到 GitHub。
