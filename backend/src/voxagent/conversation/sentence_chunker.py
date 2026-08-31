_TERMINAL_PUNCTUATION = frozenset("。！？!?；;\n")
_SAFE_COMMAS = frozenset("，,")
_MIN_STREAM_CHARS = 12
_MAX_STREAM_CHARS = 30


class SentenceChunker:
    """Buffer streamed text until it can be spoken without cutting a sentence badly."""

    def __init__(self) -> None:
        self._tail = ""

    def feed(self, text_delta: str) -> tuple[str, ...]:
        if not isinstance(text_delta, str):
            raise TypeError("text_delta must be a string")
        self._tail += text_delta
        chunks: list[str] = []
        while self._tail:
            cut = self._next_cut()
            if cut is None:
                break
            chunks.append(self._tail[:cut])
            self._tail = self._tail[cut:]
        return tuple(chunks)

    def flush(self) -> tuple[str, ...]:
        if not self._tail:
            return ()
        chunk, self._tail = self._tail, ""
        return (chunk,)

    def _next_cut(self) -> int | None:
        window = self._tail[:_MAX_STREAM_CHARS]
        for index, character in enumerate(window, start=1):
            if character in _TERMINAL_PUNCTUATION:
                return index
        comma_positions = [
            index
            for index, character in enumerate(window, start=1)
            if character in _SAFE_COMMAS and index >= _MIN_STREAM_CHARS
        ]
        if comma_positions:
            return comma_positions[-1]
        if len(self._tail) > _MAX_STREAM_CHARS:
            return _MAX_STREAM_CHARS
        return None
