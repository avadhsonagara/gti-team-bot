"""
Adaptive Card builders for GTI Teams Bot (Agentic).
"""
from datetime import datetime, timezone


def align_card_actions_to_right(card: dict) -> dict:
    """
    Format ActionSet buttons to align on the right side of item containers (Slack-style accessory button),
    using a 2-column ColumnSet layout (stretch content on left, auto button on right).
    """
    if not isinstance(card, dict) or "body" not in card or not isinstance(card["body"], list):
        return card

    def _transform_items(items: list) -> list:
        new_items = []
        for item in items:
            if not isinstance(item, dict):
                new_items.append(item)
                continue

            item_type = item.get("type")
            if item_type == "Container" and isinstance(item.get("items"), list):
                container_items = item["items"]
                action_sets = [el for el in container_items if isinstance(el, dict) and el.get("type") == "ActionSet"]
                other_elements = [el for el in container_items if isinstance(el, dict) and el.get("type") != "ActionSet"]

                if action_sets and other_elements:
                    column_set = {
                        "type": "ColumnSet",
                        "columns": [
                            {
                                "type": "Column",
                                "width": "stretch",
                                "items": other_elements,
                            },
                            {
                                "type": "Column",
                                "width": "auto",
                                "items": action_sets,
                            },
                        ],
                    }
                    container_copy = dict(item)
                    container_copy["items"] = [column_set]
                    new_items.append(container_copy)
                else:
                    container_copy = dict(item)
                    container_copy["items"] = _transform_items(container_items)
                    new_items.append(container_copy)
            else:
                new_items.append(item)
        return new_items

    card_copy = dict(card)
    card_copy["body"] = _transform_items(card["body"])
    return card_copy


def inject_quote_into_card(card: dict, quoted_query: str) -> dict:
    """
    Ensure the user's quoted query is displayed at the top of an existing Adaptive Card.
    """
    if not isinstance(card, dict) or "body" not in card:
        return card

    card_copy = dict(card)
    card_copy.setdefault("msteams", {})["width"] = "full"
    card_copy = align_card_actions_to_right(card_copy)
    if quoted_query:
        quote_element = {
            "type": "TextBlock",
            "text": quoted_query,
            "wrap": True,
            "isSubtle": True,
            "size": "Small",
        }
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
        "msteams": {
            "width": "full",
        },
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
        "msteams": {
            "width": "full",
        },
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
