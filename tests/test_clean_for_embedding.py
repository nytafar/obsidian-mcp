"""Verifies clean_for_embedding strips fenced code blocks before embedding."""
from src.services.embeddings import clean_for_embedding


def test_strips_backtick_fence():
    text = "Hello\n\n```json\n{\"a\": 1}\n```\n\nMore prose."
    out = clean_for_embedding(text)
    assert "{" not in out
    assert "Hello" in out
    assert "More prose." in out


def test_strips_tilde_fence():
    text = "Hello\n\n~~~python\nprint('hi')\n~~~\n\nMore."
    out = clean_for_embedding(text)
    assert "print" not in out
    assert "Hello" in out
    assert "More." in out


def test_preserves_inline_code():
    text = "Use `pgvector` with `<=>` for cosine."
    out = clean_for_embedding(text)
    assert out == text


def test_strips_multiple_fences():
    text = (
        "Intro\n\n```\nfirst block\n```\n\n"
        "Middle\n\n```js\nsecond block\n```\n\nEnd."
    )
    out = clean_for_embedding(text)
    assert "first block" not in out
    assert "second block" not in out
    assert "Intro" in out
    assert "Middle" in out
    assert "End." in out


def test_excalidraw_like_payload():
    """Realistic Excalidraw-shaped content: small prose + giant fenced JSON."""
    payload = "x" * 5000
    text = (
        "# Excalidraw Data\n\n## Text Elements\nLabel A\n\n"
        f"## Drawing\n```compressed-json\n{payload}\n```\n"
    )
    out = clean_for_embedding(text)
    assert payload not in out
    assert "Label A" in out
    assert len(out) < 200


def test_no_fences_unchanged():
    text = "Just prose, no code blocks at all.\n\nMultiple paragraphs."
    assert clean_for_embedding(text) == text


def test_empty_string():
    assert clean_for_embedding("") == ""


def test_only_a_fence():
    text = "```\nthe whole file is a code block\n```"
    out = clean_for_embedding(text)
    assert "code block" not in out
