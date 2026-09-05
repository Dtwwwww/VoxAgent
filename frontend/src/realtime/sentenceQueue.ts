const SENTENCE_BOUNDARY = new Set(["。", "！", "？", "!", "?", "；", ";", "\n"]);
const MAX_SENTENCE_CODE_POINTS = 80;

export class StreamingSentenceQueue {
  private pending = "";
  private cancelled = false;

  push(delta: string): string[] {
    if (this.cancelled || delta.length === 0) return [];
    this.pending += delta;
    const codePoints = Array.from(this.pending);
    const sentences: string[] = [];
    let start = 0;
    for (let index = 0; index < codePoints.length; index += 1) {
      const isBoundary = SENTENCE_BOUNDARY.has(codePoints[index]);
      const isMaximumLength = index - start + 1 >= MAX_SENTENCE_CODE_POINTS;
      if (!isBoundary && !isMaximumLength) continue;
      sentences.push(codePoints.slice(start, index + 1).join(""));
      start = index + 1;
    }
    this.pending = codePoints.slice(start).join("");
    return sentences;
  }

  cancel(): void {
    this.cancelled = true;
    this.pending = "";
  }

  reset(): void {
    this.cancelled = false;
    this.pending = "";
  }
}
