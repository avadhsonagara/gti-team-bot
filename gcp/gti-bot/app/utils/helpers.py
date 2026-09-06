import logging
import re
from typing import Optional

import orjson
from microsoft_teams.api import MessageActivityInput

logger = logging.getLogger("gti-teams-bot")

_MENTION_RE = re.compile(r"<at>.*?</at>", re.IGNORECASE)


def strip_mentions(text: str) -> str:
    """Remove all <at>...</at> mention tokens from a Teams message."""
    return _MENTION_RE.sub("", text or "")


def has_mention(text: str) -> bool:
    """True if the raw activity text contains at least one <at>...</at> mention token."""
    return bool(_MENTION_RE.search(text or ""))


# ── Adaptive Card Parser ──────────────────────────────────────────────────────

def extract_text_from_card(card: dict | None) -> str:
    """Extract plain text from an Adaptive Card dictionary for fallback delivery."""
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
    Parse model output into an Adaptive Card dict, stripping accidental code fences.

    Returns:
        (card, fallback_text) — card is None when parsing fails or the output
        isn't a valid AdaptiveCard shape.
    """
    text = (raw_text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        data = orjson.loads(text)
    except orjson.JSONDecodeError:
        return None, raw_text or "No response generated."

    if isinstance(data, dict) and data.get("type") == "AdaptiveCard" and isinstance(data.get("body"), list) and data["body"]:
        return data, extract_text_from_card(data) or "GTI report"

    logger.warning("[PARSE] Model returned JSON without a valid AdaptiveCard shape — delivering raw text")
    return None, raw_text or "No response generated."


# ── Output format instructions ────────────────────────────────────────────────

def build_custom_format_section(output_format: str) -> str:
    """
    Wrap the configured output-format instructions for injection into the prompt.

    Returns an empty string when no custom format is set, so the
    {{CUSTOM_FORMAT}} placeholder disappears rather than leaving a dangling section.
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


# ── Standard User Notices ─────────────────────────────────────────────────────

EMPTY_QUERY_NOTICE = (
    "👋 **How can I help you with threat intelligence?**\n\n"
    "Try asking something like:\n"
    "- _what do you know about 1.1.1.1?_\n"
    "- _analyze this domain: example.com_\n"
    "- _check hash: 275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0_\n"
    "- _give me threat intelligence on APT29_"
)

EMPTY_MENTION_NOTICE = EMPTY_QUERY_NOTICE

LARGE_QUERY_NOTICE = (
    "⚠️ **The GTI results for your query were too large to deliver.**\n\n"
    "Try a more specific query — for example:\n"
    "- Ask for one entity type only (e.g. _threat actors only_)\n"
    "- Reduce the number of results (e.g. _top 3 findings_)\n"
    "- Split your query into smaller parts"
)

GENERIC_DELIVERY_FAILURE_NOTICE = (
    "⚠️ Something went wrong delivering the response. Please try again."
)


def _looks_like_size_limit_error(exc: Exception) -> bool:
    """Heuristic for payload too large errors."""
    text = str(exc).lower()
    return any(
        kw in text
        for kw in ("too large", "too long", "size limit", "payload", "413", "entity too large")
    )


async def deliver_message(
    ctx,
    loading_activity_id: Optional[str],
    text: str,
    card: Optional[dict] = None,
) -> bool:
    """
    Send a response message to the Teams user.

    If loading_activity_id is provided, deletes the temporary placeholder first,
    then sends the final message as a fresh activity to avoid the Teams 'Edited' tag.
    """
    conversation_id = ctx.activity.conversation.id

    if loading_activity_id:
        try:
            await ctx.api.conversations.activities(conversation_id).delete(loading_activity_id)
        except Exception as exc:
            logger.warning("[DELIVER] Could not delete placeholder message (%s); proceeding with fresh send.", exc)

    async def _send_card(payload_card: dict) -> None:
        activity = MessageActivityInput().add_card(payload_card)
        await ctx.send(activity)

    async def _send_text(payload_text: str) -> None:
        await ctx.send(payload_text)

    # Attempt 1: Deliver Adaptive Card if provided
    if card:
        try:
            await _send_card(card)
            return True
        except Exception as exc:
            logger.warning("[DELIVER] Teams rejected Adaptive Card (%s); retrying as plain text.", exc)

    # Attempt 2: Deliver plain text
    try:
        await _send_text(text)
        return True
    except Exception as exc2:
        logger.error("[DELIVER] Teams rejected plain-text delivery (%s); sending fallback notice.", exc2)

        notice = LARGE_QUERY_NOTICE if _looks_like_size_limit_error(exc2) else GENERIC_DELIVERY_FAILURE_NOTICE
        try:
            await _send_text(notice)
            return True
        except Exception as exc3:
            logger.error("[DELIVER] Failed to deliver fallback notice: %s", exc3)
            return False
