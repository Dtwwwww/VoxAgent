import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MemoryPanel, MemoryProposalNotice } from "../MemoryPanel";
import type { LocalApiClient } from "../../localApi";
import type { MemoryProposal } from "../../useVoiceSession";

afterEach(cleanup);

function api(): LocalApiClient {
  return {
    listMemories: vi.fn(async () => [{
      id: 1,
      kind: "preference" as const,
      content: "喜欢乌龙茶",
      importance: 0.8,
      source_turn_id: 3,
      source: { message_id: 9, conversation_id: 2, turn_id: 3, content: "我喜欢乌龙茶" },
      created_at_utc: "2026-09-04T08:00:00.000Z",
      updated_at_utc: "2026-09-04T08:00:00.000Z",
    }]),
    createMemory: vi.fn(async (input) => ({
      id: 2,
      kind: input.kind,
      content: input.content,
      importance: input.importance,
      source_turn_id: input.source_turn_id ?? null,
      source: null,
      created_at_utc: "2026-09-04T08:00:00.000Z",
      updated_at_utc: "2026-09-04T08:00:00.000Z",
    })),
    updateMemory: vi.fn(),
    deleteMemory: vi.fn(async () => undefined),
    getPersona: vi.fn(),
    updatePersona: vi.fn(),
    exportData: vi.fn(),
    resetAll: vi.fn(),
    listBackups: vi.fn(async () => []),
    deleteBackup: vi.fn(),
  };
}

describe("MemoryPanel", () => {
  it("loads, shows attribution, and deletes visible memories", async () => {
    const local = api();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<MemoryPanel client={local} />);

    expect(await screen.findByText("喜欢乌龙茶")).toBeVisible();
    expect(screen.getByText("来自第 3 轮对话")).toBeVisible();
    expect(screen.getByText("原话：我喜欢乌龙茶")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "删除 喜欢乌龙茶" }));
    await waitFor(() => expect(local.deleteMemory).toHaveBeenCalledWith(1));
  });

  it("accepts or dismisses a proposed memory only after a user action", async () => {
    const local = api();
    const dismiss = vi.fn();
    const proposal: MemoryProposal = {
      id: "4:0",
      sourceTurnId: 4,
      sourceMessageId: 19,
      kind: "preference",
      content: "用户喜欢无糖咖啡",
      importance: 0.8,
      requiresConfirmation: false,
    };
    const view = render(<MemoryProposalNotice proposal={proposal} client={local} onDismiss={dismiss} />);

    fireEvent.click(screen.getByRole("button", { name: "保存记忆" }));
    await waitFor(() => expect(local.createMemory).toHaveBeenCalledWith({
      kind: "preference",
      content: "用户喜欢无糖咖啡",
      importance: 0.8,
      source_turn_id: 4,
      source_message_id: 19,
      confirmed: true,
    }));
    expect(dismiss).toHaveBeenCalledWith("4:0");

    view.rerender(<MemoryProposalNotice proposal={proposal} client={local} onDismiss={dismiss} />);
    fireEvent.click(screen.getByRole("button", { name: "不保存" }));
    expect(dismiss).toHaveBeenCalledWith("4:0");
  });
});
