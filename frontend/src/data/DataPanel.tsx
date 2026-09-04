import { useEffect, useState } from "react";

import { Icon } from "../components/Icon";
import type { BackupInfo, LocalApiClient } from "../localApi";

const RESET_PHRASE = "删除声灵全部本地数据";

export function DataPanel({ client }: { client: LocalApiClient }) {
  const [backups, setBackups] = useState<BackupInfo[]>([]);
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    void client.listBackups().then(setBackups).catch((loadError: unknown) => {
      setError(loadError instanceof Error ? loadError.message : "无法读取备份");
    });
  }, [client]);

  const run = async (operation: () => Promise<void>, success: string) => {
    setBusy(true);
    setError(null);
    try {
      await operation();
      setMessage(success);
    } catch (operationError) {
      setError(operationError instanceof Error ? operationError.message : "操作失败");
    } finally {
      setBusy(false);
    }
  };
  return <div className="settings-section data-panel">
    <div className="section-heading"><div><h3>数据与备份</h3><p>所有内容保存在 D:\VoxAgentData，不发送到云端。</p></div></div>
    <section className="data-card">
      <h4>导出数据</h4><p>导出人格、记忆、知识库文本与对话数据，向量可重新生成。</p>
      <button type="button" className="secondary-action" disabled={busy} onClick={() => { void run(() => client.exportData(), "数据已导出"); }}>导出本地数据</button>
    </section>
    <section className="data-card">
      <h4>每日备份</h4>
      {backups.length === 0 && <p>应用正常退出后每天自动备份一次，保留最近 7 份。</p>}
      {backups.map((backup) => <div className="backup-row" key={backup.filename}>
        <span><strong>{backup.date}</strong><small>{Math.max(1, Math.round(backup.size_bytes / 1024))} KB</small></span>
        <button type="button" aria-label={`删除备份 ${backup.date}`} onClick={() => { void run(async () => {
          await client.deleteBackup(backup.filename);
          setBackups((current) => current.filter((item) => item.filename !== backup.filename));
        }, "备份已删除"); }}><Icon name="trash" size={16} /></button>
      </div>)}
    </section>
    <section className="data-card data-card--danger">
      <h4>删除全部本地数据</h4>
      <p>此操作会清空人格、记忆、知识库、对话、导出文件和历史备份，无法撤销。</p>
      <label><span>请输入：{RESET_PHRASE}</span><input aria-label="删除确认短语" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /></label>
      <button type="button" className="danger-action" disabled={busy || confirmation !== RESET_PHRASE} onClick={() => { void run(async () => {
        await client.resetAll();
        setBackups([]);
        setConfirmation("");
      }, "全部本地数据已删除"); }}>删除全部本地数据</button>
    </section>
    {message && <p className="panel-success" role="status">{message}</p>}
    {error && <p className="panel-error" role="alert">{error}</p>}
  </div>;
}
