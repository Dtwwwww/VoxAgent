import { describe, expect, it } from "vitest";

import { resolveSessionToken } from "../token";

describe("resolveSessionToken", () => {
  it("uses the URL token when present", () => {
    expect(resolveSessionToken("?token=url-token", "env-token")).toBe("url-token");
  });

  it("falls back to the configured local token when the URL has none", () => {
    expect(resolveSessionToken("", "env-token")).toBe("env-token");
  });
});
