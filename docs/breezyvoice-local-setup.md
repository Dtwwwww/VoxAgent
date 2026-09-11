# BreezyVoice 台湾腔女声本地配置

前端音色列表现在包含 `台湾腔女声`（`breezy_tw_female`）。该音色适合高质量朗读，不建议用于实时通话；当前实时通话继续使用默认 Kokoro 音色。

## 本机模型位置

本机验证过的模型目录为：

```text
D:\VoxAgentData\models\speech\BreezyVoice
```

官方参考女声音频位于源码目录的 `data\example.wav`。BreezyVoice 依赖独立的 Python 3.11 运行时和 G2PW 资源；这些模型与运行时都放在 `D:\VoxAgentData`，不纳入 Git 仓库。

## 当前行为

- 音色选择器会展示并保存该音色；在合成 worker 配置完成前，试听按钮保持禁用。
- 实时通话不会调用该 CPU 慢速引擎。
- 如果后端尚未接入 BreezyVoice 合成服务，选择后文字回复仍可正常生成，但不会误触发失败的朗读/试听请求。
- 默认 Kokoro 音色始终保留为可用回退。

## 后续接入

要启用动态 BreezyVoice 朗读，需要将官方推理脚本封装成常驻本地 worker，再通过环境变量指向上述模型目录；不要把模型权重提交到 GitHub。
