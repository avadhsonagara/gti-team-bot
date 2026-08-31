"""
Adaptive Card builders for GTI Teams Bot (Agentic).
"""
from datetime import datetime, timezone


def inject_quote_into_card(card: dict, quoted_query: str) -> dict:
    """
    Ensure the user's quoted query is displayed at the top of an existing Adaptive Card.
    """
    if not quoted_query or not isinstance(card, dict) or "body" not in card:
        return card

    quote_element = {
        "type": "TextBlock",
        "text": quoted_query,
        "wrap": True,
        "isSubtle": True,
        "size": "Small",
    }
    card_copy = dict(card)
    card_copy["body"] = [quote_element] + list(card.get("body", []))
    return card_copy


def build_gti_response_card(markdown_text: str, quoted_query: str = "") -> dict:
    """
    Construct an Adaptive Card 1.5 payload displaying the GTI threat intelligence response.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    body_elements: list[dict] = []

    # Quoted original question
    if quoted_query:
        body_elements.append({
            "type": "TextBlock",
            "text": quoted_query,
            "wrap": True,
            "isSubtle": True,
            "size": "Small",
        })

    # Main threat intelligence markdown content
    body_elements.append({
        "type": "TextBlock",
        "text": markdown_text,
        "wrap": True,
    })

    # Professional footer
    body_elements.append({
        "type": "TextBlock",
        "text": f"Google Threat Intelligence • Agentic • {now}",
        "size": "Small",
        "isSubtle": True,
        "wrap": True,
        "spacing": "Medium",
    })

    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "body": body_elements,
    }


def build_status_card(text: str) -> dict:
    """
    Wrap a plain warning or informational message in a standard Adaptive Card.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "body": [
            {"type": "TextBlock", "wrap": True, "text": text},
            {
                "type": "TextBlock",
                "wrap": True,
                "isSubtle": True,
                "size": "Small",
                "text": f"GTI Teams Bot • {now}",
                "spacing": "Small",
            },
        ],
    }
