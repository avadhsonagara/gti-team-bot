"""
Teams activity handlers — dispatched from App()'s @on_message decorator.

Every inbound Teams message is routed through handle_message():
  - Processes queries directly across personal (1:1), group chat, and channel scopes.
  - Empty or whitespace-only messages -> usage hint.
  - Any query -> GTI Agentic Sessions API pipeline.
"""
import logging
from datetime import datetime, timezone
import re
from typing import Optional

from app.config import settings
from app.constants import SYSTEM_PROMPT
from app.gti.client import (
    GTIAuthenticationError,
    GTIBadRequestError,
    GTIRateLimitError,
    GTIServiceError,
    GTISessionNotFoundError,
    GTITimeoutError,
    gti_client,
)
from app.gti.session_store import get_session_id, set_session_id
from app.observability import bind_request, clear_request
from app.output_format_store import get_output_format
from app.teams.cards import (
    build_gti_response_card,
    build_status_card,
    inject_quote_into_card,
)
from app.teams.thread import get_session_key, get_team_id, get_thread_context
from app.utils.helpers import (
    EMPTY_QUERY_NOTICE,
    build_custom_format_section,
    deliver_message,
    parse_adaptive_card,
    strip_mentions,
)

logger = logging.getLogger("gti-teams-bot")


def _render_system_prompt(user_query: str, output_format: str = "") -> str:
    """Render the system prompt with user query, dynamic UTC timestamp, and output format."""
    if not SYSTEM_PROMPT:
        return user_query
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    prompt = SYSTEM_PROMPT.replace("{{CURRENT_DATETIME_UTC}}", now_utc)
    prompt = prompt.replace("{{CUSTOM_FORMAT}}", build_custom_format_section(output_format))
    if "{{USER_QUERY}}" in prompt:
        prompt = prompt.replace("{{USER_QUERY}}", user_query)
    else:
        prompt = f"{prompt}\n\nUSER QUERY:\n{user_query}"
    return prompt


def _get_sender(activity):
    """Return the Bot Framework ChannelAccount for this activity's sender."""
    return getattr(activity, "from_property", None) or getattr(activity, "from_", None)


def _get_tenant_id(activity) -> str:
    """Return the Entra (Azure AD) tenant id for this activity."""
    channel_data = getattr(activity, "channel_data", None) or {}
    if isinstance(channel_data, dict):
        tenant = channel_data.get("tenant") or {}
        if isinstance(tenant, dict) and tenant.get("id"):
            return tenant["id"]
    return getattr(activity.conversation, "tenant_id", "") or ""


def _get_conversation_scope(activity) -> str:
    """Return conversation_type ('personal', 'groupChat', 'channel', or '')."""
    return getattr(activity.conversation, "conversation_type", "") or ""


async def handle_message(ctx) -> None:
    """Single entry point for every message activity — routes to the GTI Agentic pipeline."""
    activity = ctx.activity

    raw_text = activity.text or ""
    # Strip any accidental mention tokens if present, and trim whitespace
    user_text = strip_mentions(raw_text).strip()
    tenant_id = _get_tenant_id(activity)
    conversation_id = activity.conversation.id
    sender = _get_sender(activity)
    user_id = getattr(sender, "id", "unknown") if sender else "unknown"

    bind_request(user=user_id, conversation=conversation_id, activity_id=activity.id or "")
    if tenant_id:
        bind_request(tenant=tenant_id)

    try:
        if not user_text or not re.search(r"\w", user_text, re.UNICODE):
            logger.info("[EVENT] Message with no meaningful query — replying with usage hint.")
            await ctx.send(EMPTY_QUERY_NOTICE)
            return

        await _handle_user_query(ctx, user_text, tenant_id, conversation_id)
    finally:
        clear_request()


# ── GTI Agentic Query Pipeline ───────────────────────────────────────────────

async def _handle_user_query(
    ctx,
    user_text: str,
    tenant_id: str,
    conversation_id: str,
) -> None:
    """
    Process a GTI query end-to-end:
      1. Fetch channel thread context (before posting anything of our own)
      2. Send loading placeholder
      3. Retrieve or continue the GTI Agentic session for this thread/conversation
      4. Format and deliver response as Adaptive Card
    """
    loading_activity_id: Optional[str] = None
    scope = _get_conversation_scope(ctx.activity)
    # Channel messages already show the original post inline (and, for thread
    # replies, Teams renders the reply-to preview itself) — the quoted-query
    # blockquote is only useful in personal/group chats, which have neither.
    if scope == "channel":
        quoted_query = ""
    else:
        quote_lines = [f"> {line}" for line in user_text.splitlines()] or ["> "]
        quoted_query = "\n".join(quote_lines)

    try:
        preview = user_text[:80] + ("..." if len(user_text) > 80 else "")
        logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        logger.info("[EVENT] conversation=%s scope=%s", conversation_id, scope)
        logger.info("[EVENT] query=%r", preview)

        # ── Step 1: Fetch channel thread context (before posting anything —
        # otherwise our own placeholder reply gets read right back as "history") ──
        thread_context = await get_thread_context(ctx.activity, scope)

        # ── Step 2: Send placeholder message ──────────────────────────────────
        try:
            placeholder_text = (
                f"{quoted_query}\n\n⏳ Looking into that with Google Threat Intelligence…"
                if quoted_query
                else "⏳ Looking into that with Google Threat Intelligence…"
            )
            sent = await ctx.send(placeholder_text)
            loading_activity_id = getattr(sent, "id", None)
            logger.info("[PLACEHOLDER] Posted | id=%s", loading_activity_id)
        except Exception as exc:
            logger.warning("[PLACEHOLDER] Failed (%s) — will post fresh reply directly", exc)

        # ── Step 3: Query GTI Agentic Sessions API (create or continue session) ──
        output_format = await get_output_format(settings)
        if thread_context:
            logger.info("[THREAD] Prepending channel thread context to query:\n%s", thread_context)
            contextual_query = (
                f"Recent messages in this Teams thread (oldest first):\n{thread_context}\n\n"
                f"User query:\n{user_text}"
            )
        else:
            contextual_query = user_text
        initial_msg = _render_system_prompt(user_query=contextual_query, output_format=output_format)

        # Session continuity only applies to channel threads (one GTI session
        # per thread) — personal and group chats always start a fresh
        # session per message; existing_session_id stays None there so
        # send_message() always creates a new session instead of continuing one.
        if scope == "channel":
            session_key = get_session_key(ctx.activity, scope)
            team_id = get_team_id(ctx.activity)
            existing_session_id = await get_session_id(session_key)
        else:
            session_key = ""
            team_id = ""
            existing_session_id = None
        logger.info(
            "[AGENTIC] Dispatching query to GTI Agentic API | conversation=%s session_key=%s team_id=%s mode=%s",
            conversation_id, session_key or "-", team_id or "-", "continue" if existing_session_id else "new",
        )
        session_id, response_text, _ = await gti_client.send_message(
            message=initial_msg, session_id=existing_session_id,
        )
        bind_request(session_id=session_id)
        if session_key:
            await set_session_id(session_key, session_id, team_id or None)

        # ── Step 4: Format & Deliver ──────────────────────────────────────────
        # Try parsing native Adaptive Card JSON from GTI Agent
        parsed_card, fallback_text = parse_adaptive_card(response_text)
        if parsed_card:
            logger.info("[PARSE] Successfully parsed native Adaptive Card from GTI Agent")
            card = inject_quote_into_card(parsed_card, quoted_query)
        else:
            logger.info("[PARSE] Using markdown Adaptive Card wrapper")
            card = build_gti_response_card(response_text, quoted_query=quoted_query)

        fallback_text = f"{quoted_query}\n\n{fallback_text}" if quoted_query else fallback_text

        logger.info(
            "[DELIVER] Sending response | length=%d chars mode=%s is_native_card=%s",
            len(response_text),
            "replace-placeholder" if loading_activity_id else "fresh-send",
            bool(parsed_card),
        )

        delivered = await deliver_message(ctx, loading_activity_id, fallback_text, card)
        if delivered:
            logger.info("[DONE] Response delivered successfully.", extra={"status": "delivered"})
        else:
            logger.error("[DONE] All delivery attempts failed.", extra={"status": "failed"})
        logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    except GTIAuthenticationError as exc:
        logger.error("[ERROR] GTI API key authentication failed: %s", exc)
        err_msg = "🔑 **Authentication Failed**\n\nThe Google Threat Intelligence API key is invalid or unauthorized. Please verify your `GTI_API_KEY` configuration."
        await deliver_message(ctx, loading_activity_id, err_msg, build_status_card(err_msg))

    except GTIRateLimitError as exc:
        logger.error("[ERROR] GTI rate limit exceeded: %s", exc)
        err_msg = "⚠️ **Rate Limit Exceeded**\n\nThe Google Threat Intelligence API rate limit or quota has been reached. Please try again in a moment."
        await deliver_message(ctx, loading_activity_id, err_msg, build_status_card(err_msg))

    except GTITimeoutError as exc:
        logger.error("[ERROR] GTI request timed out: %s", exc)
        err_msg = "⏱️ **Request Timed Out**\n\nThe threat intelligence query took too long to complete. Try asking a more specific question or query."
        await deliver_message(ctx, loading_activity_id, err_msg, build_status_card(err_msg))

    except GTIServiceError as exc:
        logger.error("[ERROR] GTI service unavailable: %s", exc)
        err_msg = "⚠️ **Threat Intelligence Service Unavailable**\n\nThe Google Threat Intelligence service is temporarily unreachable. Please try again shortly."
        await deliver_message(ctx, loading_activity_id, err_msg, build_status_card(err_msg))

    except GTISessionNotFoundError as exc:
        logger.error("[ERROR] GTI session not found or expired: %s", exc)
        err_msg = "🔄 **Session Expired**\n\nYour conversation session with the Google Threat Intelligence service has expired. Please start a new query."
        await deliver_message(ctx, loading_activity_id, err_msg, build_status_card(err_msg))

    except GTIBadRequestError as exc:
        logger.error("[ERROR] GTI rejected the request: %s", exc)
        err_msg = "🚫 **Request Rejected**\n\nThe Google Threat Intelligence service could not process this query. Try rephrasing your question."
        await deliver_message(ctx, loading_activity_id, err_msg, build_status_card(err_msg))

    except Exception:
        logger.exception("[ERROR] Unexpected error in GTI message handler.")
        err_msg = "⚠️ **Something went wrong while processing your request.** Please try again."
        await deliver_message(ctx, loading_activity_id, err_msg, build_status_card(err_msg))
