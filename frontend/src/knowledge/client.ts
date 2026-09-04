export interface KnowledgeDocument {
  id: number;
  display_name: string;
  sha256: string;
  mime_type: string;
  imported_at_utc: string;
  chunk_count: number;
}

export interface KnowledgeImportResult {
  document_id: number;
  display_name: string;
  created: boolean;
  chunk_count: number;
  sha256: string;
}

export interface KnowledgeChunk {
  id: number;
  ordinal: number;
  page_number: number | null;
  content: string;
}

export interface KnowledgeClient {
  listDocuments(): Promise<KnowledgeDocument[]>;
  importDocument(file: File): Promise<KnowledgeImportResult>;
  deleteDocument(documentId: number): Promise<void>;
  listChunks(documentId: number): Promise<KnowledgeChunk[]>;
}

async function requireSuccess(response: Response): Promise<Response> {
  if (response.ok) return response;
  let message = "本地知识库操作失败，请重试";
  try {
    const payload = await response.json() as { detail?: string };
    if (payload.detail) message = payload.detail;
  } catch {
    // Keep the stable local error message when the response is not JSON.
  }
  throw new Error(message);
}

export function createKnowledgeClient(baseUrl: string, token: string): KnowledgeClient {
  const authorization = { Authorization: `Bearer ${token}` };
  return {
    async listDocuments() {
      const response = await requireSuccess(await fetch(`${baseUrl}/v1/knowledge`, {
        headers: authorization,
      }));
      return await response.json() as KnowledgeDocument[];
    },
    async importDocument(file) {
      const query = new URLSearchParams({ filename: file.name });
      const response = await requireSuccess(await fetch(`${baseUrl}/v1/knowledge?${query}`, {
        method: "POST",
        headers: { ...authorization, "Content-Type": "application/octet-stream" },
        body: file,
      }));
      return await response.json() as KnowledgeImportResult;
    },
    async deleteDocument(documentId) {
      await requireSuccess(await fetch(`${baseUrl}/v1/knowledge/${documentId}`, {
        method: "DELETE",
        headers: authorization,
      }));
    },
    async listChunks(documentId) {
      const response = await requireSuccess(await fetch(
        `${baseUrl}/v1/knowledge/${documentId}/chunks?limit=20`,
        { headers: authorization },
      ));
      return await response.json() as KnowledgeChunk[];
    },
  };
}
