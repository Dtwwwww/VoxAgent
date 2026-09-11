import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { KnowledgePanel } from "../KnowledgePanel";
import type { KnowledgeClient } from "../client";

afterEach(cleanup);

function client(): KnowledgeClient {
  return {
    listDocuments: vi.fn(async () => [{
      id: 7,
      display_name: "产品说明.md",
      sha256: "a".repeat(64),
      mime_type: "text/markdown",
      imported_at_utc: "2026-09-04T08:00:00.000Z",
      chunk_count: 3,
    }]),
    importDocument: vi.fn(async (file: File) => ({
      document_id: 8,
      display_name: file.name,
      created: true,
      chunk_count: 2,
      sha256: "b".repeat(64),
    })),
    cancelImport: vi.fn(async () => undefined),
    deleteDocument: vi.fn(async () => undefined),
    listChunks: vi.fn(async () => [{
      id: 11,
      ordinal: 0,
      page_number: 2,
      content: "这是可核对的来源片段",
    }]),
  };
}

describe("KnowledgePanel", () => {
  it("loads documents only after the panel is opened", async () => {
    const api = client();
    const view = render(<KnowledgePanel open={false} client={api} onClose={vi.fn()} />);

    expect(api.listDocuments).not.toHaveBeenCalled();
    view.rerender(<KnowledgePanel open client={api} onClose={vi.fn()} />);

    expect(await screen.findByText("产品说明.md")).toBeVisible();
    expect(screen.getByText("3 个知识片段")).toBeVisible();
  });

  it("imports a selected supported document and refreshes the list", async () => {
    const api = client();
    render(<KnowledgePanel open client={api} onClose={vi.fn()} />);
    await screen.findByText("产品说明.md");
    const file = new File(["本地内容"], "我的资料.txt", { type: "text/plain" });

    fireEvent.change(screen.getByLabelText("选择本地文档"), { target: { files: [file] } });

    await waitFor(() => expect(api.importDocument).toHaveBeenCalledWith(file, expect.any(AbortSignal)));
    await waitFor(() => expect(api.listDocuments).toHaveBeenCalledTimes(2));
    expect(screen.getByText("“我的资料.txt”已导入本地知识库")).toBeVisible();
  });

  it("deletes a document after explicit confirmation", async () => {
    const api = client();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<KnowledgePanel open client={api} onClose={vi.fn()} />);
    await screen.findByText("产品说明.md");

    fireEvent.click(screen.getByRole("button", { name: "删除 产品说明.md" }));

    await waitFor(() => expect(api.deleteDocument).toHaveBeenCalledWith(7));
  });

  it("lets the user cancel a long-running import", async () => {
    const api = client();
    vi.mocked(api.importDocument).mockImplementation(async () => await new Promise(() => undefined));
    render(<KnowledgePanel open client={api} onClose={vi.fn()} />);
    await screen.findByText("产品说明.md");
    const file = new File(["本地内容"], "大文档.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText("选择本地文档"), { target: { files: [file] } });

    fireEvent.click(await screen.findByRole("button", { name: "取消导入" }));

    await waitFor(() => expect(api.cancelImport).toHaveBeenCalledOnce());
  });

  it("lets the user inspect attributed source excerpts", async () => {
    const api = client();
    render(<KnowledgePanel open client={api} onClose={vi.fn()} />);
    await screen.findByText("产品说明.md");

    fireEvent.click(screen.getByRole("button", { name: "查看 产品说明.md 的片段" }));

    expect(await screen.findByText("这是可核对的来源片段")).toBeVisible();
    expect(screen.getByText("第 2 页 · 片段 1")).toBeVisible();
  });
});
