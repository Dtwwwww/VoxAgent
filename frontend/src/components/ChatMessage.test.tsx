import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChatMessage } from "./ChatMessage";

afterEach(cleanup);

describe("ChatMessage sources", () => {
  it("shows reviewable memory origins and knowledge excerpts", () => {
    render(<ChatMessage
      message={{
        id: "assistant-1",
        turnId: 1,
        role: "assistant",
        origin: "assistant",
        text: "建议用八十度水温。",
        status: "complete",
        sources: [
          { kind: "memory", id: 2, content: "用户喜欢茶", sourceText: "我喜欢喝茶", sourceTurnId: 3 },
          { kind: "knowledge", chunkId: 7, documentId: 4, displayName: "手册.pdf", content: "八十度水温", pageNumber: 5 },
        ],
      }}
      speaking={false}
      onSpeak={vi.fn()}
      onStopSpeaking={vi.fn()}
    />);

    fireEvent.click(screen.getByText("查看引用来源（2）"));

    expect(screen.getByText("原话：我喜欢喝茶")).toBeVisible();
    expect(screen.getByText("手册.pdf · 第 5 页")).toBeVisible();
    expect(screen.queryByText(/D:\\/)).not.toBeInTheDocument();
  });
});
