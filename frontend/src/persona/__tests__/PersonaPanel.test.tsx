import "@testing-library/jest-dom/vitest";

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import type { LocalApiClient, PersonaResponse } from "../../localApi";
import { PersonaPanel } from "../PersonaPanel";

afterEach(cleanup);

const persona: PersonaResponse = {
  revision: 0,
  config: {
    name: "声灵",
    user_address: "用户",
    background: "本地伙伴",
    traits: "温和、可靠",
    relationship: "陪伴与助手",
    style: "简洁、口语化",
    initiative: "适度主动",
    boundaries: "尊重用户",
    default_reply_length: "两到四个短句",
  },
};

it("loads and saves the single active persona", async () => {
  const client = {
    getPersona: vi.fn(async () => persona),
    updatePersona: vi.fn(async (request) => ({ config: request.config, revision: 1 })),
  } as unknown as LocalApiClient;
  render(<PersonaPanel client={client} />);

  const name = await screen.findByRole("textbox", { name: "名称" });
  fireEvent.change(name, { target: { value: "小灵" } });
  fireEvent.click(screen.getByRole("button", { name: "保存人格设置" }));

  await waitFor(() => expect(client.updatePersona).toHaveBeenCalled());
  expect(screen.getByText("人格设置已保存，下次回复立即生效")).toBeVisible();
});
