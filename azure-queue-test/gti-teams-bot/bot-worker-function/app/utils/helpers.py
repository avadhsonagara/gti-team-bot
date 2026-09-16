import json
import logging
import re
from typing import Optional

from app.teams.cards import align_card_actions_to_right

logger = logging.getLogger("gti-teams-bot")

_MENTION_RE = re.compile(r"<at>.*?</at>", re.IGNORECASE)


def strip_mentions(text: str) -> str:
    """Remove all <at>...</at> mention tokens from a Teams message."""
    return _MENTION_RE.sub("", text or "")


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
    # Strip a code fence anywhere in the text (not just at the start), in
    # case the model prefaces the JSON with prose. Falls back to stripping
    # just a leading fence when there's no matching close (e.g. output
    # truncated before the closing ```) — a matched pair is preferred
    # whenever both exist.
    fence_start = text.find("```")
    if fence_start != -1:
        fence_end = text.rfind("```")
        if fence_end > fence_start:
            text = re.sub(r"^[a-zA-Z]*\n?", "", text[fence_start + 3:fence_end]).strip()
        elif text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text).strip()
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


def build_thread_context_section(thread_context: str) -> str:
    """
    Wrap the fetched Teams thread history for injection into the prompt.

    Returns an empty string when there is no prior thread context (personal/group
    chats, or the first message in a channel thread), so the {{THREAD_CONTEXT}}
    placeholder disappears rather than leaving a dangling section.
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
# The empty-query notice lives in bot-ingest-function/function_app.py instead
# (as _EMPTY_QUERY_NOTICE) — the ingest function is what actually handles an
# empty/no-content query, before a job is ever enqueued to this worker.

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
    Detect a payload-too-large failure delivering to Teams. Checks the HTTP
    status code first (bot_client.py's send/update/delete all raise via
    response.raise_for_status(), which carries the real status on
    exc.response) and falls back to a text heuristic for exceptions that
    don't carry a response (e.g. connection errors).
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
    return {"type": "message", "text": text}


def _card_activity(card: dict) -> dict:
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
    Send a response message to the Teams user.

    If loading_activity_id is provided:
      - edit_in_place=True: updates that placeholder activity in place with
        the final message. Used for channel threads, where deleting a
        message leaves a "This message has been deleted." tombstone visible
        to the whole channel — updating it instead only adds a small
        "(Edited)" label.
      - edit_in_place=False (default): deletes the placeholder first, then
        sends the final message as a fresh activity, avoiding the "Edited"
        tag entirely. Used for personal/group chats, where a deleted
        message leaves no trace anyway.
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
