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
    expect(queue.push("新回答。")).toEqual([]);
    expect(queue.flush()).toEqual(["新回答。"]);
  });

  it("splits a long remainder at 80 Unicode code points without breaking surrogate pairs", () => {
    const queue = new StreamingSentenceQueue();
    const longText = `${"你".repeat(79)}😀后续`;

    expect(queue.push(longText)).toEqual(["你".repeat(79)]);
    expect(queue.push("。" )).toEqual([]);
    expect(queue.flush()).toEqual(["后续。"]);
  });

  it("flushes one trimmed unterminated tail exactly once", () => {
    const queue = new StreamingSentenceQueue();

    expect(queue.push("  这是没有句号的结尾  ")).toEqual([]);
    expect(queue.flush()).toEqual(["这是没有句号的结尾"]);
    expect(queue.flush()).toEqual([]);
  });

  it("removes emoji and presentation-only symbols without emitting a separate utterance", () => {
    const queue = new StreamingSentenceQueue();

    expect(queue.push("你好😀，继续✨。" )).toEqual(["你好，继续。"]);
    expect(queue.push("😀✨。" )).toEqual([]);
    expect(queue.flush()).toEqual([]);
  });

  it("holds chunked fenced code until the closing fence and never speaks its contents", () => {
    const queue = new StreamingSentenceQueue();

    expect(queue.push("先说明。```ts\nconst query = 'a?" )).toEqual([]);
    expect(queue.push("b';\nconsole.log(query);\n```\n继续说明。" )).toEqual(['先说明。继续说明。']);
  });

  it("does not split chunked markdown links or raw URL query strings", () => {
    const queue = new StreamingSentenceQueue();

    expect(queue.push("请看[使用说明？](https://example.com/search?q=声" )).toEqual([]);
    expect(queue.push("灵?lang=zh)再继续。" )).toEqual(['请看使用说明？再继续。']);
    expect(queue.push("地址 https://example.com/search?q=声灵?lang=zh 后续。" )).toEqual([
      '地址 链接 后续。',
    ]);
  });

  it("defers the 80-code-point cap until an open URL reaches a safe raw boundary", () => {
    const queue = new StreamingSentenceQueue();
    const prefix = "甲".repeat(76);
    const url = "https://example.com/search?q=one?lang=zh";

    expect(queue.pushSegments(`${prefix} ${url} 后续。`)).toEqual([
      { text: `${prefix} 链接`, sourceCodePoints: Array.from(`${prefix} ${url} `).length },
      { text: "后续。", sourceCodePoints: 3 },
    ]);
  });

  it("maps normalized segments to exact raw code-point spans across hidden constructs", () => {
    const queue = new StreamingSentenceQueue();
    const first = "Hi😀。";
    const remainder = "```js\nconst q = 'x?y';\n```\n请看 [文档](https://example.com?q=a?b) 继续。";

    expect(queue.pushSegments(first + remainder)).toEqual([
      { text: "Hi。请看 文档 继续。", sourceCodePoints: Array.from(first + remainder).length },
    ]);
  });

  it("merges short adjacent sentences before emitting speech", () => {
    const queue = new StreamingSentenceQueue();

    expect(queue.push("你好。" )).toEqual([]);
    expect(queue.push("这是声灵。" )).toEqual(["你好。这是声灵。"]);
  });

  it("flushes a short tail at assistant.done", () => {
    const queue = new StreamingSentenceQueue();

    expect(queue.push("好的。" )).toEqual([]);
    expect(queue.flush()).toEqual(["好的。"]);
  });
});
