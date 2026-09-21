"""
Adaptive Card payload generators for Microsoft Teams.

Constructs schema-compliant Adaptive Cards (version 1.5) for GTI responses,
status notifications, and layout adjustments such as right-aligning action buttons.
"""
from datetime import datetime, timezone


def align_card_actions_to_right(card: dict) -> dict:
    """
    Format ActionSet buttons to align on the right side of container elements using ColumnSets.

    Args:
        card: Adaptive Card payload dictionary.

    Returns:
        Transformed card dictionary with right-aligned action buttons.
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
    Inject a quoted user query at the top of an Adaptive Card body.

    Args:
        card: Original Adaptive Card payload dictionary.
        quoted_query: Formatted markdown quote string to prepend.

    Returns:
        Updated Adaptive Card payload dictionary.
    """
    if not isinstance(card, dict) or "body" not in card:
        return card

    card_copy = dict(card)
    card_copy["msteams"] = dict(card_copy.get("msteams") or {})
    card_copy["msteams"]["width"] = "full"
    card_copy = align_card_actions_to_right(card_copy)
    if quoted_query:
        quote_element = {
            "type": "TextBlock",
            "text": quoted_query,
            "wrap": True,
            "isSubtle": True,
            "size": "Small",
        }
        card_copy["body"] = [quote_element] + list(card_copy.get("body", []))
    return card_copy


def build_gti_response_card(markdown_text: str, quoted_query: str = "") -> dict:
    """
    Construct an Adaptive Card payload displaying a GTI threat intelligence response.

    Args:
        markdown_text: Formatted response markdown content.
        quoted_query: Optional quoted user query string to display at the top.

    Returns:
        Adaptive Card dictionary ready for delivery to Microsoft Teams.
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


def build_status_card(text: str, quoted_query: str = "") -> dict:
    """
    Construct an Adaptive Card payload displaying a status, warning, or error notice.

    Args:
        text: Status or error notification message text.
        quoted_query: Optional quoted user query string to display at the top.

    Returns:
        Adaptive Card dictionary ready for delivery to Microsoft Teams.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body_elements: list[dict] = []
    if quoted_query:
        body_elements.append({
            "type": "TextBlock",
            "text": quoted_query,
            "wrap": True,
            "isSubtle": True,
            "size": "Small",
        })
    body_elements.append({"type": "TextBlock", "wrap": True, "text": text})
    body_elements.append({
        "type": "TextBlock",
        "wrap": True,
        "isSubtle": True,
        "size": "Small",
        "text": f"GTI Teams Bot • {now}",
        "spacing": "Small",
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
