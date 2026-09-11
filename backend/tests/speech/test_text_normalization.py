from voxagent.speech.text_normalization import normalize_tts_text, split_tts_text


def test_normalize_removes_emoji_urls_and_markdown_noise():
    source = "今天天气很好😀。[GitHub](https://github.com) `print(1)` https://example.com"

    assert normalize_tts_text(source) == "今天天气很好。GitHub print(1) 链接"


def test_normalize_keeps_url_queries_silent_without_eating_chinese_punctuation():
    source = "地址 https://example.com/search?q=voice?lang=zh。继续 `inline?code`。"

    assert normalize_tts_text(source) == "地址 链接。继续 inline?code。"


def test_normalize_drops_an_unterminated_fenced_code_tail():
    source = "先说明。```python\nprint('secret?query')"

    assert normalize_tts_text(source) == "先说明。"


def test_split_tts_text_honors_sentence_and_length_boundaries():
    source = "第一句，继续解释；第二句？" + "很长的内容" * 30

    parts = split_tts_text(source, max_chars=40)

    assert parts[0] == "第一句，继续解释；第二句？"
    assert all(1 <= len(part) <= 40 for part in parts)
    assert "".join(parts) == source
