export type MemoryKind = "preference" | "profile" | "habit" | "relationship" | "event";

export interface MemoryRecord {
  id: number;
  kind: MemoryKind;
  content: string;
  importance: number;
  source_turn_id: number | null;
  source: {
    message_id: number;
    conversation_id: number;
    turn_id: number;
    content: string;
  } | null;
  created_at_utc: string;
  updated_at_utc: string;
}

export interface MemoryCreate {
  kind: MemoryKind;
  content: string;
  importance: number;
  source_turn_id?: number;
  source_message_id?: number;
  confirmed: boolean;
}

export interface PersonaConfig {
  name: string;
  user_address: string;
  background: string;
  traits: string;
  relationship: string;
  style: string;
  initiative: string;
  boundaries: string;
  default_reply_length: string;
}

export interface PersonaResponse {
  config: PersonaConfig;
  revision: number;
}

export interface BackupInfo {
  filename: string;
  date: string;
  size_bytes: number;
}

export interface ToolAuditRecord {
  id: number;
  request_id: number;
  session_id: string;
  turn_id: number;
  call_id: string;
  tool_name: string;
  event_type: string;
  detail: {
    duration_ms?: number;
    error_code?: string | null;
    recovered_from?: string;
  };
  created_at_utc: string;
}

export interface LocalApiClient {
  listMemories(): Promise<MemoryRecord[]>;
  createMemory(input: MemoryCreate): Promise<MemoryRecord>;
  updateMemory(id: number, input: { content: string; importance: number; expected_updated_at_utc: string }): Promise<MemoryRecord>;
  deleteMemory(id: number): Promise<void>;
  getPersona(): Promise<PersonaResponse>;
  updatePersona(input: { expected_revision: number; config: PersonaConfig }): Promise<PersonaResponse>;
  exportData(): Promise<void>;
  resetAll(): Promise<void>;
  listBackups(): Promise<BackupInfo[]>;
  deleteBackup(filename: string): Promise<void>;
  listToolAudit(limit?: number, offset?: number): Promise<ToolAuditRecord[]>;
}

async function checked(response: Response): Promise<Response> {
  if (response.ok) return response;
  let message = "本地数据操作失败，请重试";
  try {
    const body = await response.json() as { detail?: string | { message?: string } };
    if (typeof body.detail === "string") message = body.detail;
    else if (body.detail?.message) message = body.detail.message;
  } catch {
    // Keep the stable message for non-JSON failures.
  }
  throw new Error(message);
}

export function createLocalApiClient(baseUrl: string, token: string): LocalApiClient {
  const headers = { Authorization: `Bearer ${token}` };
  const jsonHeaders = { ...headers, "Content-Type": "application/json" };
  const json = async <T>(url: string, init?: RequestInit): Promise<T> =>
    await (await checked(await fetch(`${baseUrl}${url}`, init))).json() as T;
  return {
    listMemories: () => json("/v1/memories", { headers }),
    createMemory: (input) => json("/v1/memories", {
      method: "POST", headers: jsonHeaders, body: JSON.stringify(input),
    }),
    updateMemory: (id, input) => json(`/v1/memories/${id}`, {
      method: "PATCH", headers: jsonHeaders, body: JSON.stringify(input),
    }),
    async deleteMemory(id) {
      await checked(await fetch(`${baseUrl}/v1/memories/${id}`, { method: "DELETE", headers }));
    },
    getPersona: () => json("/v1/persona", { headers }),
    updatePersona: (input) => json("/v1/persona", {
      method: "PUT", headers: jsonHeaders, body: JSON.stringify(input),
    }),
    async exportData() {
      const ticket = await json<{ download_id: string }>("/v1/data/export", {
        method: "POST", headers,
      });
      const response = await checked(await fetch(
        `${baseUrl}/v1/data/export/${encodeURIComponent(ticket.download_id)}`,
        { headers },
      ));
      const objectUrl = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = "voxagent-export.zip";
      anchor.click();
      URL.revokeObjectURL(objectUrl);
    },
    async resetAll() {
      await checked(await fetch(`${baseUrl}/v1/data`, {
        method: "DELETE",
        headers: jsonHeaders,
        body: JSON.stringify({ confirmation: "删除声灵全部本地数据" }),
      }));
    },
    listBackups: () => json("/v1/backups", { headers }),
    listToolAudit: (limit = 50, offset = 0) => json(
      `/v1/tool-audit?limit=${encodeURIComponent(limit)}&offset=${encodeURIComponent(offset)}`,
      { headers },
    ),
    async deleteBackup(filename) {
      await checked(await fetch(`${baseUrl}/v1/backups/${encodeURIComponent(filename)}`, {
        method: "DELETE", headers,
      }));
    },
  };
}
