# 文本输入静默回复设计

## 目标

用户通过文本框发送消息时，Agent 只输出文字，不自动播放语音。语音输入仍自动播放语音回复；每条已完成的 Agent 消息继续提供“朗读”按钮，用户可按需手动播放。

## 交互规则

- 文本输入：显示用户文字和 Agent 文字，固定不自动朗读。
- 语音输入：显示识别文字和 Agent 文字，并自动播放 Agent 语音。
- 手动朗读：已完成的 Agent 消息继续显示“朗读”按钮，可重复点击播放。
- 设置界面：移除“文字回复自动朗读”复选框，避免出现与固定规则冲突的控制项。
- 旧设置：忽略浏览器中曾保存的 `speakTextReplies` 值；保留音色和语速设置。

## 数据流

`Composer` 提交文字后，`useVoiceSession.submitText` 始终发送 `text.submit` 且 `speak_response` 为 `false`。语音输入继续由后端 `commit_audio` 路径调用 `_start_reply(..., speak_response=True)`。手动“朗读”继续发送 `assistant.speak`，不经过文本自动朗读策略。

## 代码边界

- `frontend/src/components/Composer.tsx`：移除自动朗读复选框。
- `frontend/src/useVoiceSession.ts`：移除公开的自动朗读状态/修改方法，并将文字提交固定为静默回复。
- `frontend/src/voiceSettings.ts`：不再读取或保存 `speakTextReplies`，仅保留音色和语速。
- 前端相关测试：验证文字请求固定为 `speak_response: false`，界面没有自动朗读开关，语音和手动朗读路径不受影响。
- 后端协议和编排器保持不变，以免扩大本次改动范围。

## 错误处理与兼容性

旧版 localStorage 数据可以包含 `speakTextReplies`，读取时忽略该字段，不影响音色和语速恢复。文字发送、语音输入和手动朗读继续沿用现有连接错误处理。

## 验收标准

1. 无论旧设置是否启用自动朗读，文本发送都携带 `speak_response: false`。
2. 页面不再显示“文字回复自动朗读”开关。
3. 语音输入仍请求自动语音回复。
4. Agent 消息上的“朗读”按钮仍可使用并可重复播放。
5. 聚焦前端测试、TypeScript 类型检查和 Vite 构建通过。
