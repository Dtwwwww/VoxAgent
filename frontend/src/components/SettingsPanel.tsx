import { useEffect, useState } from "react";

import { DataPanel } from "../data/DataPanel";
import type { LocalApiClient } from "../localApi";
import { MemoryPanel } from "../memory/MemoryPanel";
import { PersonaPanel } from "../persona/PersonaPanel";
import { Icon } from "./Icon";
import { ToolAuditPanel } from "./ToolAuditPanel";

type SettingsTab = "persona" | "memory" | "data" | "audit";

export function SettingsPanel({
  open,
  client,
  onClose,
  onReset,
}: {
  open: boolean;
  client: LocalApiClient;
  onClose(): void;
  onReset(): void;
}) {
  const [tab, setTab] = useState<SettingsTab>("persona");
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);
  if (!open) return null;
  return <div className="settings-layer" role="presentation" onMouseDown={(event) => {
    if (event.target === event.currentTarget) onClose();
  }}>
    <section className="settings-panel" role="dialog" aria-modal="true" aria-labelledby="settings-title">
      <header className="settings-panel__header">
        <div><h2 id="settings-title">声灵设置</h2><p>管理人格、长期记忆和本地数据。</p></div>
        <button type="button" className="icon-button" aria-label="关闭设置" onClick={onClose}><Icon name="close" /></button>
      </header>
      <div className="settings-tabs" role="tablist" aria-label="设置分类">
        {(["persona", "memory", "data", "audit"] as const).map((value) => <button
          type="button"
          role="tab"
          aria-selected={tab === value}
          key={value}
          onClick={() => setTab(value)}
        >{{ persona: "人格", memory: "记忆", data: "数据", audit: "工具审计" }[value]}</button>)}
      </div>
      <div className="settings-panel__body">
        {tab === "persona" && <PersonaPanel client={client} />}
        {tab === "memory" && <MemoryPanel client={client} />}
        {tab === "data" && <DataPanel client={client} onReset={onReset} />}
        {tab === "audit" && <ToolAuditPanel client={client} />}
      </div>
    </section>
  </div>;
}
