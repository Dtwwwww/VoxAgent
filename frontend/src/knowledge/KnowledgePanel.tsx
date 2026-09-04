import { useEffect, useRef, useState } from "react";

import { Icon } from "../components/Icon";
import type { KnowledgeClient, KnowledgeDocument } from "./client";

interface KnowledgePanelProps {
  open: boolean;
  client: KnowledgeClient;
  onClose(): void;
}

export function KnowledgePanel({ open, client, onClose }: KnowledgePanelProps) {
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [loading, setLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const loadDocuments = async () => {
    setLoading(true);
    setError(null);
    try {
      setDocuments(await client.listDocuments());
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "无法读取本地知识库");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!open) return;
    void loadDocuments();
  }, [open, client]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onClose]);

  if (!open) return null;

  const importFile = async (file: File | undefined) => {
    if (!file) return;
    setImporting(true);
    setError(null);
    setMessage(null);
    try {
      const result = await client.importDocument(file);
      setMessage(result.created
        ? `“${result.display_name}”已导入本地知识库`
        : `“${result.display_name}”已经存在`);
      await loadDocuments();
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "文档导入失败");
    } finally {
      setImporting(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  const deleteDocument = async (document: KnowledgeDocument) => {
    if (!window.confirm(`确定从知识库中删除“${document.display_name}”吗？`)) return;
    setError(null);
    try {
      await client.deleteDocument(document.id);
      setDocuments((current) => current.filter((item) => item.id !== document.id));
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "删除失败");
    }
  };

  return <div className="knowledge-layer" role="presentation" onMouseDown={(event) => {
    if (event.target === event.currentTarget) onClose();
  }}>
    <section className="knowledge-panel" role="dialog" aria-modal="true" aria-labelledby="knowledge-title">
      <header className="knowledge-panel__header">
        <div>
          <h2 id="knowledge-title">本地知识库</h2>
          <p>导入的内容只在这台电脑上解析和检索。</p>
        </div>
        <button type="button" className="icon-button" aria-label="关闭知识库" onClick={onClose}>
          <Icon name="close" />
        </button>
      </header>

      <label className="knowledge-upload" aria-disabled={importing}>
        <Icon name={importing ? "spinner" : "upload"} />
        <span>{importing ? "正在解析并建立索引…" : "添加文档"}</span>
        <input
          ref={inputRef}
          type="file"
          aria-label="选择本地文档"
          accept=".txt,.md,.markdown,.pdf,.docx"
          disabled={importing}
          onChange={(event) => { void importFile(event.target.files?.[0]); }}
        />
      </label>
      <p className="knowledge-panel__hint">支持 TXT、Markdown、文本 PDF、DOCX，单个文件不超过 20 MB</p>

      {message && <p className="knowledge-panel__success" role="status">{message}</p>}
      {error && <p className="knowledge-panel__error" role="alert">{error}</p>}

      <div className="knowledge-list" aria-busy={loading || importing}>
        {loading && documents.length === 0 && <p className="knowledge-empty">正在读取知识库…</p>}
        {!loading && documents.length === 0 && <div className="knowledge-empty">
          <Icon name="book" size={28} />
          <strong>还没有导入文档</strong>
          <span>添加资料后，声灵会在回答时检索相关片段。</span>
        </div>}
        {documents.map((document) => <article className="knowledge-card" key={document.id}>
          <span className="knowledge-card__icon"><Icon name="file" /></span>
          <div className="knowledge-card__copy">
            <strong title={document.display_name}>{document.display_name}</strong>
            <span>{document.chunk_count} 个知识片段</span>
          </div>
          <button
            type="button"
            className="knowledge-card__delete"
            aria-label={`删除 ${document.display_name}`}
            onClick={() => { void deleteDocument(document); }}
          >
            <Icon name="trash" size={18} />
          </button>
        </article>)}
      </div>
    </section>
  </div>;
}
