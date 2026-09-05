from voxagent.speech.text_normalization import normalize_tts_text, split_tts_text


def test_normalize_removes_emoji_urls_and_markdown_noise():
    source = "今天天气很好😀。[GitHub](https://github.com) `print(1)` https://example.com"

    assert normalize_tts_text(source) == "今天天气很好。GitHub print(1) 链接"


def test_split_tts_text_honors_sentence_and_length_boundaries():
    source = "第一句，继续解释；第二句？" + "很长的内容" * 30

    parts = split_tts_text(source, max_chars=40)

    assert parts[0] == "第一句，继续解释；第二句？"
    assert all(1 <= len(part) <= 40 for part in parts)
    assert "".join(parts) == source
