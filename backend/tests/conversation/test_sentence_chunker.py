from voxagent.conversation.sentence_chunker import SentenceChunker


def test_chunker_emits_terminal_punctuation_and_retains_tail():
    chunker = SentenceChunker()

    assert chunker.feed("你好。下一") == ("你好。",)
    assert chunker.feed("句还没结束") == ()
    assert chunker.flush() == ("下一句还没结束",)
    assert chunker.flush() == ()


def test_chunker_prefers_a_safe_comma_in_the_streaming_window():
    chunker = SentenceChunker()
    text = "这是第一段需要稳定输出的中文内容，我们继续补充后续说明"

    chunks = chunker.feed(text)

    assert chunks == ("这是第一段需要稳定输出的中文内容，",)
    assert chunker.flush() == ("我们继续补充后续说明",)


def test_chunker_hard_splits_mixed_text_without_safe_punctuation():
    chunker = SentenceChunker()
    text = "这是一个没有逗号也没有句号的混合 English streaming payload 用来证明长度限制"
    chunks = chunker.feed(text)

    assert chunks
    assert all(12 <= len(chunk) <= 30 for chunk in chunks)
    assert "".join((*chunks, *chunker.flush())) == text
