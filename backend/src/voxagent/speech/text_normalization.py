from __future__ import annotations

import re
import textwrap

_URL_RE = re.compile(
    r"\b(?:https?://|www\.)[^\s\]\)<>'\"`。！？；，]+", re.IGNORECASE
)
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((?:https?://|www\.)[^\s)]+\)")
_CODE_FENCE_RE = re.compile(r"```[\s\S]*?(?:```|$)")
_INLINE_CODE_RE = re.compile(r"`([^`]*)`?")
_KEYCAP_RE = re.compile(r"[0-9#*]\ufe0f?\u20e3")
_EMOJI_RE = re.compile(
    r"[\U0001F1E6-\U0001F1FF"  # flags
    r"\U0001F300-\U0001FAFF"  # symbols + pictographs
    r"\u2600-\u27BF"           # dingbats + misc
    r"\u200d\ufe0f\u20e3"      # joiner, presentation and keycap marks
    r"]",
)

_SENTENCE_BOUNDARIES = "。！？!?；;\n"
_SHORT_BOUNDARY = "，,."


def normalize_tts_text(text: str) -> str:
    """Normalize assistant text for a natural, stable TTS payload.

    - Remove fenced code blocks while preserving inline code bodies.
    - Keep link labels and drop URLs.
    - Replace raw links by "链接" to preserve sentence cadence.
    - Remove emoji presentation symbols.
    - Collapse whitespace and normalize common markdown artifacts.
    """
    if not isinstance(text, str):
        return ""
    normalized = textwrap.dedent(text)
    normalized = _CODE_FENCE_RE.sub("", normalized)
    normalized = _INLINE_CODE_RE.sub(r"\1", normalized)
    normalized = _MARKDOWN_LINK_RE.sub(r"\1", normalized)
    normalized = _URL_RE.sub("链接", normalized)
    normalized = strip_emoji(normalized)
    normalized = normalized.replace("#", "，").replace("*", "，")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def strip_emoji(text: str) -> str:
    """Remove emoji sequences without changing ordinary assistant formatting."""
    return _EMOJI_RE.sub("", _KEYCAP_RE.sub("", text))


def split_tts_text(text: str, *, max_chars: int = 120) -> tuple[str, ...]:
    """Split TTS-ready text by punctuation and length for stable chunk synthesis."""
    source = text.strip()
    if not source:
        return ()
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    chunks: list[str] = []
    while source:
        segment = source[:max_chars]
        cut = max(
            [
                position + 1
                for position, character in enumerate(segment)
                if character in _SENTENCE_BOUNDARIES
            ],
            default=0,
        )
        if cut == 0:
            for separator in _SHORT_BOUNDARY:
                candidate = source[:max_chars].rfind(separator)
                if candidate > 0:
                    cut = candidate + 1
                    break
        if cut == 0:
            cut = min(len(source), max_chars)
        chunk = source[:cut].strip()
        source = source[cut:].strip()
        if chunk:
            chunks.append(chunk)
        if not source:
            break
        if len(chunk) == 0 and len(source) >= max_chars:
            source = source[max_chars:].strip()
    return tuple(chunks)
