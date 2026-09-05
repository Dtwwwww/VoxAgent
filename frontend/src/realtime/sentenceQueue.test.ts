import { describe, expect, it } from "vitest";

import { StreamingSentenceQueue } from "./sentenceQueue";

describe("StreamingSentenceQueue", () => {
  it("emits complete Chinese sentences in arrival order", () => {
    const queue = new StreamingSentenceQueue();

    expect(queue.push("你好，我是声灵。今天")).toEqual(["你好，我是声灵。"]);
    expect(queue.push("想和你聊天！")).toEqual(["今天想和你聊天！"]);
  });

  it("does not resume a cancelled response until reset", () => {
    const queue = new StreamingSentenceQueue();

    queue.cancel();
    expect(queue.push("旧回答不应继续。")).toEqual([]);
    queue.reset();
    expect(queue.push("新回答。")).toEqual(["新回答。"]);
  });

  it("splits a long remainder at 80 Unicode code points without breaking surrogate pairs", () => {
    const queue = new StreamingSentenceQueue();
    const longText = `${"你".repeat(79)}😀后续`;

    expect(queue.push(longText)).toEqual([`${"你".repeat(79)}😀`]);
    expect(queue.push("。" )).toEqual(["后续。"]);
  });
});
