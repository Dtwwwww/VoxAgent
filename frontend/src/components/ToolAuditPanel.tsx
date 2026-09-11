import { useEffect, useState } from "react";

import type { LocalApiClient, ToolAuditRecord } from "../localApi";

export function ToolAuditPanel({ client }: { client: LocalApiClient }) {
  const [records, setRecords] = useState<ToolAuditRecord[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let current = true;
    setFailed(false);
    void client.listToolAudit().then((items) => {
      if (current) setRecords(items);
    }).catch(() => {
      if (current) setFailed(true);
    });
    return () => { current = false; };
  }, [client, attempt]);

  if (failed) return <div className="panel-empty">
    <p>加载工具审计失败。</p>
    <button type="button" onClick={() => setAttempt((value) => value + 1)}>重试</button>
  </div>;
  if (records === null) return <p className="panel-empty">正在加载工具审计……</p>;
  if (records.length === 0) return <p className="panel-empty">还没有工具审计记录。</p>;
  return <ol className="tool-audit-list">
    {records.map((record) => <li key={record.id}>
      <header><strong>{record.tool_name}</strong><time>{record.created_at_utc}</time></header>
      <p>{record.event_type} · {record.session_id} / {record.turn_id}</p>
      <div className="tool-audit-list__detail">
        {typeof record.detail.duration_ms === "number" && <span>{record.detail.duration_ms} ms</span>}
        {typeof record.detail.error_code === "string" && <span>错误 {record.detail.error_code}</span>}
        {typeof record.detail.recovered_from === "string" && <span>恢复自 {record.detail.recovered_from}</span>}
      </div>
    </li>)}
  </ol>;
}
