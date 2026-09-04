import { useEffect, useState } from "react";

import { Icon } from "../components/Icon";
import type { LocalApiClient, MemoryKind, MemoryRecord } from "../localApi";
import type { MemoryProposal } from "../useVoiceSession";

const KIND_LABELS: Record<MemoryKind, string> = {
  preference: "偏好",
  profile: "档案",
  habit: "习惯",
  relationship: "关系",
  event: "事件",
};

export function MemoryProposalNotice({
  proposal,
  client,
  onDismiss,
}: {
  proposal: MemoryProposal;
  client: LocalApiClient;
  onDismiss(id: string): void;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await client.createMemory({
        kind: proposal.kind,
        content: proposal.content,
        importance: proposal.importance,
        source_turn_id: proposal.sourceTurnId,
        confirmed: true,
      });
      onDismiss(proposal.id);
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "记忆保存失败");
    } finally {
      setSaving(false);
    }
  };
  return <aside className="memory-proposal" aria-label="记忆建议">
    <div><strong>要记住这件事吗？</strong><span>{proposal.content}</span></div>
    {error && <span className="panel-error" role="alert">{error}</span>}
    <div className="memory-proposal__actions">
      <button type="button" onClick={() => onDismiss(proposal.id)}>不保存</button>
      <button type="button" className="primary-action" disabled={saving} onClick={() => { void save(); }}>
        {saving ? "保存中…" : "保存记忆"}
      </button>
    </div>
  </aside>;
}

export function MemoryPanel({ client }: { client: LocalApiClient }) {
  const [items, setItems] = useState<MemoryRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [content, setContent] = useState("");
  const [kind, setKind] = useState<MemoryKind>("preference");
  const [editing, setEditing] = useState<number | null>(null);
  const [editContent, setEditContent] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      setItems(await client.listMemories());
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "无法读取记忆");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { void load(); }, [client]);

  const add = async () => {
    if (!content.trim()) return;
    try {
      const created = await client.createMemory({
        kind, content: content.trim(), importance: 0.8, confirmed: true,
      });
      setItems((current) => [created, ...current.filter((item) => item.id !== created.id)]);
      setContent("");
      setAdding(false);
    } catch (addError) {
      setError(addError instanceof Error ? addError.message : "保存失败");
    }
  };

  const saveEdit = async (item: MemoryRecord) => {
    try {
      const updated = await client.updateMemory(item.id, {
        content: editContent.trim(),
        importance: item.importance,
        expected_updated_at_utc: item.updated_at_utc,
      });
      setItems((current) => current.map((candidate) => candidate.id === item.id ? updated : candidate));
      setEditing(null);
    } catch (editError) {
      setError(editError instanceof Error ? editError.message : "更新失败");
    }
  };

  const remove = async (item: MemoryRecord) => {
    if (!window.confirm(`确定删除“${item.content}”吗？`)) return;
    try {
      await client.deleteMemory(item.id);
      setItems((current) => current.filter((candidate) => candidate.id !== item.id));
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "删除失败");
    }
  };

  return <div className="settings-section">
    <div className="section-heading">
      <div><h3>长期记忆</h3><p>你可以查看、修改或删除声灵记住的内容。</p></div>
      <button type="button" className="secondary-action" onClick={() => setAdding((value) => !value)}>添加记忆</button>
    </div>
    {adding && <div className="inline-form">
      <select aria-label="记忆类型" value={kind} onChange={(event) => setKind(event.target.value as MemoryKind)}>
        {Object.entries(KIND_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
      </select>
      <textarea aria-label="记忆内容" value={content} maxLength={500} onChange={(event) => setContent(event.target.value)} />
      <button type="button" className="primary-action" onClick={() => { void add(); }}>保存</button>
    </div>}
    {error && <p className="panel-error" role="alert">{error}</p>}
    {loading && <p className="panel-empty">正在读取记忆…</p>}
    {!loading && items.length === 0 && <p className="panel-empty">还没有长期记忆。</p>}
    <div className="memory-list">
      {items.map((item) => <article className="memory-card" key={item.id}>
        <div className="memory-card__meta"><span>{KIND_LABELS[item.kind]}</span>{item.source_turn_id && <span>来自第 {item.source_turn_id} 轮对话</span>}</div>
        {editing === item.id
          ? <textarea aria-label={`编辑 ${item.content}`} value={editContent} onChange={(event) => setEditContent(event.target.value)} />
          : <p>{item.content}</p>}
        <div className="memory-card__actions">
          {editing === item.id
            ? <button type="button" onClick={() => { void saveEdit(item); }}>保存修改</button>
            : <button type="button" onClick={() => { setEditing(item.id); setEditContent(item.content); }}>编辑</button>}
          <button type="button" aria-label={`删除 ${item.content}`} onClick={() => { void remove(item); }}><Icon name="trash" size={16} />删除</button>
        </div>
      </article>)}
    </div>
  </div>;
}
