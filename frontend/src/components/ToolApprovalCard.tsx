import { useState } from "react";

import type { ToolActivity, ToolApproval } from "../useVoiceSession";

const TOOL_LABELS: Readonly<Record<string, string>> = {
  "knowledge.search": "搜索知识库",
  "files.search_authorized": "搜索授权文件",
  "reminders.list": "查看提醒",
  "reminders.create": "创建提醒",
  "reminders.complete": "完成提醒",
  "apps.open_allowlisted": "打开允许的应用",
};

function toolLabel(toolName: string) {
  return TOOL_LABELS[toolName] ?? "执行本地工具";
}

export function ToolApprovalCard({
  approval,
  onConfirm,
  onDeny,
}: {
  approval: ToolApproval;
  onConfirm(): void;
  onDeny(): void;
}) {
  const [choiceSent, setChoiceSent] = useState(approval.status === "submitted");
  const choose = (action: () => void) => {
    setChoiceSent(true);
    action();
  };
  return <section className="tool-approval" role="region" aria-label="工具执行确认">
    <div className="tool-approval__copy">
      <strong>{toolLabel(approval.toolName)}</strong>
      <code>{approval.toolName}</code>
      <span>{approval.permission} · 需要确认</span>
      <p>此权限仅允许本次执行。</p>
    </div>
    <div className="tool-approval__actions">
      <button type="button" disabled={choiceSent} onClick={() => choose(onConfirm)}>允许一次</button>
      <button type="button" disabled={choiceSent} onClick={() => choose(onDeny)}>拒绝</button>
    </div>
  </section>;
}

export function ToolActivityStatus({ activity }: { activity: ToolActivity | undefined }) {
  if (!activity) return null;
  const status = activity.status === "running"
    ? "正在执行"
    : activity.status === "completed" ? "已完成" : "已拒绝或失败";
  return <p className="tool-activity" role="status">
    <span>{toolLabel(activity.toolName)}</span>
    <strong>{status}</strong>
  </p>;
}
