import { useEffect, useState } from "react";

import type { LocalApiClient, PersonaConfig } from "../localApi";

const FIELDS: Array<{ key: keyof PersonaConfig; label: string; multiline?: boolean }> = [
  { key: "name", label: "名称" },
  { key: "user_address", label: "对用户的称呼" },
  { key: "background", label: "背景", multiline: true },
  { key: "traits", label: "性格", multiline: true },
  { key: "relationship", label: "关系定位", multiline: true },
  { key: "style", label: "表达风格", multiline: true },
  { key: "initiative", label: "主动程度", multiline: true },
  { key: "boundaries", label: "边界", multiline: true },
  { key: "default_reply_length", label: "默认回复长度" },
];

export function PersonaPanel({ client }: { client: LocalApiClient }) {
  const [config, setConfig] = useState<PersonaConfig | null>(null);
  const [revision, setRevision] = useState(0);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    void client.getPersona().then((response) => {
      setConfig(response.config);
      setRevision(response.revision);
    }).catch((loadError: unknown) => {
      setError(loadError instanceof Error ? loadError.message : "无法读取人格设置");
    });
  }, [client]);
  if (!config) return <p className="panel-empty">{error ?? "正在读取人格设置…"}</p>;

  const save = async () => {
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const response = await client.updatePersona({ expected_revision: revision, config });
      setConfig(response.config);
      setRevision(response.revision);
      setMessage("人格设置已保存，下次回复立即生效");
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };
  return <div className="settings-section persona-form">
    <div className="section-heading"><div><h3>人格设置</h3><p>声灵始终使用这一套人格处理陪伴与助手请求。</p></div></div>
    {FIELDS.map((field) => <label key={field.key}>
      <span>{field.label}</span>
      {field.multiline
        ? <textarea value={config[field.key]} onChange={(event) => setConfig({ ...config, [field.key]: event.target.value })} />
        : <input value={config[field.key]} onChange={(event) => setConfig({ ...config, [field.key]: event.target.value })} />}
    </label>)}
    {message && <p className="panel-success" role="status">{message}</p>}
    {error && <p className="panel-error" role="alert">{error}</p>}
    <button type="button" className="primary-action" disabled={saving} onClick={() => { void save(); }}>{saving ? "保存中…" : "保存人格设置"}</button>
  </div>;
}
