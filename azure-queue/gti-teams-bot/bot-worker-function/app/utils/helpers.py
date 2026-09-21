"""
Utility functions and helpers for the GTI Teams Bot worker.

Provides text extraction, mention cleaning, Adaptive Card JSON parsing,
prompt formatting utilities, and Teams message delivery with multi-stage fallbacks.
"""
import json
import logging
import re
from typing import Optional

from app.teams.cards import align_card_actions_to_right

logger = logging.getLogger("gti-teams-bot")

_MENTION_RE = re.compile(r"<at>.*?</at>", re.IGNORECASE)


def strip_mentions(text: str) -> str:
    """
    Remove all <at>...</at> mention tokens from a Teams message.

    Args:
        text: Input message text.

    Returns:
        Cleaned text string with mention tags removed.
    """
    return _MENTION_RE.sub("", text or "")


# ── Adaptive Card Parser ──────────────────────────────────────────────────────

def extract_text_from_card(card: dict | None) -> str:
    """
    Extract plain text from an Adaptive Card dictionary for fallback delivery.

    Args:
        card: Adaptive Card dictionary or None.

    Returns:
        Concatenated text extracted from text blocks and fact sets.
    """
    if not isinstance(card, dict):
        return ""
    parts: list[str] = []

    def _walk(node) -> None:
        if isinstance(node, dict):
            node_type = node.get("type")
            if node_type == "TextBlock" and node.get("text"):
                parts.append(str(node["text"]))
            elif node_type == "FactSet":
                for fact in node.get("facts", []) or []:
                    if isinstance(fact, dict) and (fact.get("title") or fact.get("value")):
                        parts.append(f"{fact.get('title', '')}: {fact.get('value', '')}")
            for key in ("body", "items", "columns"):
                for child in node.get(key, []) or []:
                    _walk(child)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(card.get("body", []))
    return "\n\n".join(parts)


def parse_adaptive_card(raw_text: str | None) -> tuple[Optional[dict], str]:
    """
    Parse model output into an Adaptive Card dictionary, stripping accidental code fences.

    Args:
        raw_text: Raw text or JSON string from the model output.

    Returns:
        Tuple of (card_dict, fallback_text). card_dict is None if parsing fails.
    """
    text = (raw_text or "").strip()
    if not text:
        return None, "No response generated."

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None, raw_text or "No response generated."

    if isinstance(data, dict) and data.get("type") == "AdaptiveCard" and isinstance(data.get("body"), list) and data["body"]:
        data.setdefault("msteams", {})["width"] = "full"
        return data, extract_text_from_card(data) or "GTI report"

    logger.warning("[PARSE] Model returned JSON without a valid AdaptiveCard shape — delivering raw text")
    return None, raw_text or "No response generated."


# ── Output format instructions ────────────────────────────────────────────────

def build_custom_format_section(output_format: str) -> str:
    """
    Wrap custom output formatting instructions for injection into the prompt template.

    Args:
        output_format: Configured output format instructions string.

    Returns:
        Formatted markdown section string, or empty string if no custom format is set.
    """
    if not output_format.strip():
        return ""
    logger.info("[PROMPT] Custom output format applied (%d chars)", len(output_format))
    return (
        "---\n\n"
        "## CUSTOM OUTPUT FORMAT INSTRUCTIONS\n"
        "Apply these instructions IN ADDITION TO the Adaptive Card rules above.\n"
        "They refine presentation only — they do not replace the Adaptive Card structure.\n\n"
        "─── CUSTOM FORMAT ───\n"
        f"{output_format.strip()}\n"
        "────────────────────"
    )


def build_thread_context_section(thread_context: str) -> str:
    """
    Wrap thread history transcript for injection into the prompt template.

    Args:
        thread_context: Formatted thread messages transcript.

    Returns:
        Formatted markdown section string, or empty string if thread context is empty.
    """
    if not thread_context.strip():
        return ""
    return (
        "## CONVERSATION CONTEXT\n\n"
        "Recent messages in this Teams thread, oldest first "
        "(for resolving follow-ups and pronouns):\n"
        f"{thread_context.strip()}\n\n"
        "---\n\n"
    )


# ── Standard User Notices ─────────────────────────────────────────────────────

LARGE_QUERY_NOTICE = (
    "⚠️ **Response Too Large to Deliver**\n\n"
    "The results exceed Microsoft Teams message size limits. Try a more specific query — for example:\n"
    "- Ask for one entity type only (e.g. _threat actors only_)\n"
    "- Request fewer items (e.g. _top 3 findings_)\n"
    "- Split your question into smaller parts"
)

GENERIC_DELIVERY_FAILURE_NOTICE = (
    "⚠️ **Delivery Failed**\n\nUnable to display the response in Teams. Please try submitting your question again."
)


def _looks_like_size_limit_error(exc: Exception) -> bool:
    """
    Detect whether a delivery failure was caused by exceeding Teams payload size limits.

    Args:
        exc: Exception caught during message delivery.

    Returns:
        True if the exception indicates an HTTP 413 or payload size limit error.
    """
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is not None:
        return status_code == 413

    text = str(exc).lower()
    return any(
        kw in text
        for kw in ("too large", "too long", "size limit", "payload", "413", "entity too large")
    )


def _text_activity(text: str) -> dict:
    """
    Construct a plain text message activity payload.

    Args:
        text: Message text content.

    Returns:
        Bot Framework message activity dictionary.
    """
    return {"type": "message", "text": text}


def _card_activity(card: dict) -> dict:
    """
    Construct a message activity payload containing an Adaptive Card attachment.

    Args:
        card: Adaptive Card payload dictionary.

    Returns:
        Bot Framework message activity dictionary with card attachment.
    """
    return {
        "type": "message",
        "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": card}],
    }


def deliver_message(
    ctx,
    loading_activity_id: Optional[str],
    text: str,
    card: Optional[dict] = None,
    edit_in_place: bool = False,
) -> bool:
    """
    Deliver a response message to Microsoft Teams with fallback handling.

    Args:
        ctx: Context wrapper for sending messages to Teams.
        loading_activity_id: Activity ID of the placeholder message to update or delete.
        text: Fallback plain text content.
        card: Optional Adaptive Card payload dictionary.
        edit_in_place: If True, updates placeholder in place; if False, deletes and reposts.

    Returns:
        True if delivery succeeded, False if all delivery fallback attempts failed.
    """
    conversation_id = ctx.activity.conversation.id
    activities = ctx.api.conversations.activities(conversation_id)

    if loading_activity_id and not edit_in_place:
        try:
            activities.delete(loading_activity_id)
        except Exception as exc:
            logger.warning("[DELIVER] Could not delete placeholder message (%s); proceeding with fresh send.", exc)

    def _deliver(activity: dict) -> None:
        if loading_activity_id and edit_in_place:
            activities.update(loading_activity_id, activity)
        else:
            ctx.send(activity)

    # Attempt 1: Deliver Adaptive Card if provided
    if card:
        try:
            if isinstance(card, dict):
                card.setdefault("msteams", {})["width"] = "full"
                card = align_card_actions_to_right(card)
            _deliver(_card_activity(card))
            return True
        except Exception as exc:
            logger.warning("[DELIVER] Teams rejected Adaptive Card (%s); retrying as plain text.", exc)

    # Attempt 2: Deliver plain text
    try:
        _deliver(_text_activity(text))
        return True
    except Exception as exc2:
        logger.error("[DELIVER] Teams rejected plain-text delivery (%s); sending fallback notice.", exc2)

        notice = LARGE_QUERY_NOTICE if _looks_like_size_limit_error(exc2) else GENERIC_DELIVERY_FAILURE_NOTICE
        try:
            _deliver(_text_activity(notice))
            return True
        except Exception as exc3:
            logger.error("[DELIVER] Failed to deliver fallback notice: %s", exc3)

            # Placeholder updates can fail (e.g. an activity too old to edit)
            # in ways a fresh send wouldn't — don't leave the "Looking into
            # that…" placeholder stuck forever; fall back to delete + send.
            if loading_activity_id and edit_in_place:
                try:
                    activities.delete(loading_activity_id)
                except Exception:
                    pass
                try:
                    ctx.send(_text_activity(notice))
                    return True
                except Exception as exc4:
                    logger.error("[DELIVER] Fallback delete+send also failed: %s", exc4)

            return False
