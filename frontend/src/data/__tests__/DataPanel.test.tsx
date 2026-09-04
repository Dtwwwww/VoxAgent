import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import type { LocalApiClient } from "../../localApi";
import { DataPanel } from "../DataPanel";

afterEach(cleanup);

it("exports data and gates full reset behind the exact phrase", async () => {
  const client = {
    exportData: vi.fn(async () => undefined),
    resetAll: vi.fn(async () => undefined),
    listBackups: vi.fn(async () => [{ filename: "voxagent-2026-09-04.db", date: "2026-09-04", size_bytes: 1024 }]),
    deleteBackup: vi.fn(async () => undefined),
  } as unknown as LocalApiClient;
  const onReset = vi.fn();
  render(<DataPanel client={client} onReset={onReset} />);

  expect(await screen.findByText("2026-09-04")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "导出本地数据" }));
  await waitFor(() => expect(client.exportData).toHaveBeenCalledOnce());
  const reset = screen.getByRole("button", { name: "删除全部本地数据" });
  expect(reset).toBeDisabled();
  fireEvent.change(screen.getByRole("textbox", { name: "删除确认短语" }), {
    target: { value: "删除声灵全部本地数据" },
  });
  expect(reset).toBeEnabled();
  fireEvent.click(reset);
  await waitFor(() => expect(client.resetAll).toHaveBeenCalledOnce());
  expect(onReset).toHaveBeenCalledOnce();
});
