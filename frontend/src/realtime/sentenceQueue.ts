const SENTENCE_BOUNDARY = new Set(["。", "！", "？", "!", "?", "；", ";", "\n"]);
const MAX_SENTENCE_CODE_POINTS = 80;
const URL = /\b(?:https?:\/\/|www\.)[^\s\]\)<>\'"`。！？；，]+/gi;
const MARKDOWN_LINK = /\[([^\]]+)\]\((?:https?:\/\/|www\.)[^\s)]+\)/giu;
const CODE_FENCE = /```[\s\S]*?(?:```|$)/gu;
const INLINE_CODE = /`([^`]*)`?/gu;
const KEYCAP = /[0-9#*]\ufe0f?\u20e3/gu;
const EMOJI = /[\u{1F1E6}-\u{1F1FF}\u{1F300}-\u{1FAFF}\u2600-\u27BF\u200D\uFE0F\u20E3]/gu;
const SPOKEN_CONTENT = /[\p{L}\p{N}]/u;
const URL_PREFIXES = ["https://", "http://", "www."] as const;
const URL_TERMINATORS = new Set(["]", ")", "<", ">", "'", '"', "`", "。", "！", "？", "；", "，"]);

export interface StreamingSpeechSegment {
  text: string;
  sourceCodePoints: number;
}

export function normalizeBrowserSpeechText(text: string): string {
  return text
    .replace(CODE_FENCE, "")
    .replace(INLINE_CODE, "$1")
    .replace(MARKDOWN_LINK, "$1")
    .replace(URL, "链接")
    .replace(KEYCAP, "")
    .replace(EMOJI, "")
    .replaceAll("#", "，")
    .replaceAll("*", "，")
    .replace(/\s+/gu, " ")
    .trim();
}

type PrefixMatch = "complete" | "partial" | "none";

function urlPrefixMatch(codePoints: string[], start: number): PrefixMatch {
  const remaining = codePoints.slice(start, start + 8).join("").toLowerCase();
  if (start > 0 && /[A-Za-z0-9_]/u.test(codePoints[start - 1])) return "none";
  if (URL_PREFIXES.some((prefix) => remaining.startsWith(prefix))) return "complete";
  return URL_PREFIXES.some((prefix) => prefix.startsWith(remaining)) ? "partial" : "none";
}

function sequenceIndex(codePoints: string[], sequence: string[], start: number): number {
  const maximum = codePoints.length - sequence.length;
  for (let index = start; index <= maximum; index += 1) {
    if (sequence.every((character, offset) => codePoints[index + offset] === character)) return index;
  }
  return -1;
}

function urlEnd(codePoints: string[], start: number): number | null {
  for (let index = start; index < codePoints.length; index += 1) {
    if (/\s/u.test(codePoints[index]) || URL_TERMINATORS.has(codePoints[index])) return index;
  }
  return null;
}

function markdownLinkEnd(codePoints: string[], start: number): number | null | undefined {
  const labelEnd = codePoints.indexOf("]", start + 1);
  if (labelEnd < 0) return undefined;
  if (labelEnd + 1 >= codePoints.length) return undefined;
  if (codePoints[labelEnd + 1] !== "(") return null;
  const destinationStart = labelEnd + 2;
  const prefix = urlPrefixMatch(codePoints, destinationStart);
  if (prefix === "partial") return undefined;
  if (prefix === "none") return null;
  for (let index = destinationStart; index < codePoints.length; index += 1) {
    if (codePoints[index] === ")") return index + 1;
    if (/\s/u.test(codePoints[index])) return null;
  }
  return undefined;
}

export class StreamingSentenceQueue {
  private pending = "";
  private cancelled = false;
  private skippedSourceCodePoints = 0;

  push(delta: string): string[] {
    return this.pushSegments(delta).map((segment) => segment.text);
  }

  pushSegments(delta: string): StreamingSpeechSegment[] {
    if (this.cancelled || delta.length === 0) return [];
    this.pending += delta;
    const codePoints = Array.from(this.pending);
    const sentences: StreamingSpeechSegment[] = [];
    let start = 0;
    for (let index = 0; index < codePoints.length; index += 1) {
      if (
        codePoints[index] === "`"
        && codePoints[index + 1] === "`"
        && codePoints[index + 2] === "`"
      ) {
        const closingFence = sequenceIndex(codePoints, ["`", "`", "`"], index + 3);
        if (closingFence < 0) break;
        index = closingFence + 2;
        continue;
      }
      if (codePoints[index] === "`") {
        const closingInline = codePoints.indexOf("`", index + 1);
        if (closingInline < 0) break;
        index = closingInline;
        continue;
      }
      if (codePoints[index] === "[") {
        const linkEnd = markdownLinkEnd(codePoints, index);
        if (linkEnd === undefined) break;
        if (linkEnd !== null) {
          index = linkEnd - 1;
          continue;
        }
      }
      const prefix = urlPrefixMatch(codePoints, index);
      if (prefix === "partial") break;
      if (prefix === "complete") {
        const end = urlEnd(codePoints, index);
        if (end === null) break;
        index = end - 1;
        continue;
      }
      const isBoundary = SENTENCE_BOUNDARY.has(codePoints[index]);
      const isMaximumLength = index - start + 1 >= MAX_SENTENCE_CODE_POINTS;
      if (!isBoundary && !isMaximumLength) continue;
      this.appendSegment(sentences, codePoints.slice(start, index + 1).join(""));
      start = index + 1;
    }
    this.pending = codePoints.slice(start).join("");
    return sentences;
  }

  flush(): string[] {
    return this.flushSegments().map((segment) => segment.text);
  }

  flushSegments(): StreamingSpeechSegment[] {
    if (this.cancelled || this.pending.length === 0) return [];
    const source = this.pending;
    this.pending = "";
    const segments: StreamingSpeechSegment[] = [];
    this.appendSegment(segments, source);
    return segments;
  }

  cancel(): void {
    this.cancelled = true;
    this.pending = "";
    this.skippedSourceCodePoints = 0;
  }

  reset(): void {
    this.cancelled = false;
    this.pending = "";
    this.skippedSourceCodePoints = 0;
  }

  private appendSegment(segments: StreamingSpeechSegment[], source: string): void {
    const sourceCodePoints = Array.from(source).length;
    const text = normalizeBrowserSpeechText(source);
    if (!SPOKEN_CONTENT.test(text)) {
      this.skippedSourceCodePoints += sourceCodePoints;
      return;
    }
    segments.push({
      text,
      sourceCodePoints: this.skippedSourceCodePoints + sourceCodePoints,
    });
    this.skippedSourceCodePoints = 0;
  }
}
