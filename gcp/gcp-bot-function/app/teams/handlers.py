"""
Teams activity handlers — dispatched by main.py's gti_bot_http for every
inbound "message" activity.

Every inbound Teams message is routed through handle_message():
  - Processes queries directly across personal (1:1), group chat, and channel scopes.
  - Empty or whitespace-only messages -> usage hint.
  - Any query -> GTI Agentic Sessions API pipeline.
"""
import logging
import threading
import time
from datetime import datetime, timezone
import re
from typing import Optional

from app.config import settings
from app.constants import SYSTEM_PROMPT
from app.gti.client import (
    GTIAuthenticationError,
    GTIClientError,
    GTIEmptyResponseError,
    GTIPayloadTooLargeError,
    GTIRateLimitError,
    GTIServiceError,
    GTITimeoutError,
    gti_client,
)
from app.gti.session_store import (
    delete_team_sessions,
    get_session_id,
    get_team_id_for_channel,
    set_session_id,
)
from app.observability import bind_request, clear_request
from app.output_format_store import get_output_format
from app.teams.attachments import download_attachments
from app.teams.cards import (
    build_gti_response_card,
    build_status_card,
    inject_quote_into_card,
)
from app.teams.thread import get_channel_id, get_session_key, get_team_id, get_thread_context
from app.utils.helpers import (
    EMPTY_QUERY_NOTICE,
    build_custom_format_section,
    build_thread_context_section,
    deliver_message,
    parse_adaptive_card,
    strip_mentions,
)

logger = logging.getLogger("gti-teams-bot")

# Dedup guard: Bot Framework can retry an inbound activity if it doesn't get
# a prompt 2xx (our GTI round-trip can take well over a minute), which would
# otherwise be processed a second time concurrently on this same instance

_DEDUP_WINDOW_SECONDS = 300.0
_claimed_activity_ids: dict[str, float] = {}
_claimed_activity_ids_guard = threading.Lock()


def _claim_activity(activity_id: str) -> bool:
    """Return True if this activity id may proceed (not already claimed/recent)."""
    if not activity_id:
        return True  # nothing to dedup against — let it through
    now = time.monotonic()
    with _claimed_activity_ids_guard:
        for stale_id in [aid for aid, expires_at in _claimed_activity_ids.items() if expires_at <= now]:
            del _claimed_activity_ids[stale_id]
        if activity_id in _claimed_activity_ids:
            return False
        _claimed_activity_ids[activity_id] = now + _DEDUP_WINDOW_SECONDS
        return True


def _render_system_prompt(user_query: str, thread_context: str = "", output_format: str = "") -> str:
    """Render the system prompt with user query, thread context, dynamic UTC timestamp, and output format."""
    if not SYSTEM_PROMPT:
        return user_query
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    prompt = SYSTEM_PROMPT.replace("{{CURRENT_DATETIME_UTC}}", now_utc)
    prompt = prompt.replace("{{THREAD_CONTEXT}}", build_thread_context_section(thread_context))
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


def handle_message(ctx) -> None:
    """Single entry point for every message activity — routes to the GTI Agentic pipeline."""
    activity = ctx.activity

    if not _claim_activity(activity.id or ""):
        logger.warning(
            "[DEDUP] Activity %s is already being processed or was recently completed — skipping duplicate delivery.",
            activity.id,
        )
        return

    raw_text = activity.text or ""
    # Strip any accidental mention tokens if present, and trim whitespace
    user_text = strip_mentions(raw_text).strip()
    tenant_id = _get_tenant_id(activity)
    conversation_id = activity.conversation.id
    sender = _get_sender(activity)
    user_id = getattr(sender, "id", "unknown") if sender else "unknown"
    user_name = (getattr(sender, "name", None) or user_id) if sender else "unknown"

    bind_request(user=user_id, user_name=user_name, conversation=conversation_id, activity_id=activity.id or "")
    if tenant_id:
        bind_request(tenant=tenant_id)

    try:
        scope = _get_conversation_scope(activity)

        if not user_text or not re.search(r"\w", user_text, re.UNICODE):
            logger.info("[EVENT] Message with no meaningful query | user='%s' — replying with usage hint.", user_name)
            deliver_message(ctx, None, EMPTY_QUERY_NOTICE, build_status_card(EMPTY_QUERY_NOTICE))
            return

        _handle_user_query(ctx, user_text, tenant_id, conversation_id, scope, user_id, user_name)
    finally:
        clear_request()


# ── GTI Agentic Query Pipeline ───────────────────────────────────────────────

def _handle_user_query(
    ctx,
    user_text: str,
    tenant_id: str,
    conversation_id: str,
    scope: str,
    user_id: str,
    user_name: str,
) -> None:
    """
    Process a GTI query end-to-end:
      1. Download user file/image attachments
      2. Fetch channel thread context (before posting anything of our own —
         GCP has no is_placeholder_message()-style filter the way Azure's
         thread.py does, so this ordering — not filtering — is what keeps
         our own placeholder from being read back as prior history)
      3. Send loading placeholder
      4. Retrieve or continue the GTI Agentic session for this thread/conversation
      5. Format and deliver response as Adaptive Card
    """
    t_start = time.perf_counter()
    loading_activity_id: Optional[str] = None
    # Channel messages already show the original post inline (and, for thread
    # replies, Teams renders the reply-to preview itself) — the quoted-query
    # blockquote is only useful in personal/group chats, which have neither.
    if scope == "channel":
        quoted_query = ""
    else:
        quote_lines = [f"> {line}" for line in user_text.splitlines()] or ["> "]
        quoted_query = "\n".join(quote_lines)

    try:
        logger.info(
            "[WORKER START] Processing User Query | user='%s' (%s) scope=%s | query='%s'",
            user_name, user_id, scope, user_text,
        )

        # ── Step 1: Download user file/image attachments ───────────────────
        t_att = time.perf_counter()
        attachments = download_attachments(ctx)
        att_summary = f"count={len(attachments)}"
        if attachments:
            att_names = [a[0] for a in attachments if isinstance(a, tuple) and a and a[0]]
            if att_names:
                att_summary += f" ({', '.join(att_names)})"
        logger.info(
            "[WORKER 1/5] Attachments processed in %.0fms | %s",
            (time.perf_counter() - t_att) * 1000, att_summary,
        )

        # ── Step 2: Fetch channel thread context ────────────────────────────
        t_thread = time.perf_counter()
        thread_context = get_thread_context(ctx.activity, scope)
        logger.info(
            "[WORKER 2/5] Thread context processed in %.0fms | active=%s chars=%d",
            (time.perf_counter() - t_thread) * 1000, bool(thread_context), len(thread_context),
        )

        # ── Step 3: Send placeholder message ────────────────────────────────
        t_placeholder = time.perf_counter()
        try:
            placeholder_text = (
                f"{quoted_query}\n\n⏳ Looking into that …"
                if quoted_query
                else "⏳ Looking into that …"
            )
            sent = ctx.send(placeholder_text)
            loading_activity_id = getattr(sent, "id", None)
            logger.info(
                "[WORKER 3/5] Placeholder posted in %.0fms | id=%s",
                (time.perf_counter() - t_placeholder) * 1000, loading_activity_id,
            )
        except Exception as exc:
            logger.warning(
                "[WORKER 3/5] Placeholder post failed (%.0fms): %s — will post fresh reply directly",
                (time.perf_counter() - t_placeholder) * 1000, exc,
            )

        # ── Step 4: Query GTI Agentic Sessions API (create or continue session) ──
        output_format = get_output_format(settings)
        initial_msg = _render_system_prompt(
            user_query=user_text, thread_context=thread_context, output_format=output_format,
        )

        # Session continuity only applies to channel threads (one GTI session
        # per thread) — personal and group chats always start a fresh
        # session per message; existing_session_id stays None there so
        # send_message() always creates a new session instead of continuing one.
        if scope == "channel":
            session_key = get_session_key(ctx.activity, scope)
            team_id = get_team_id(ctx.activity)
            channel_id = get_channel_id(ctx.activity)
        else:
            session_key = ""
            team_id = ""
            channel_id = ""

        t_gti = time.perf_counter()
        existing_session_id = get_session_id(team_id, channel_id, session_key) if session_key else None
        logger.info(
            "[WORKER 4/5] Dispatching query to GTI Agentic API | mode=%s session_id=%s user='%s' prompt_chars=%d",
            "continue" if existing_session_id else "new", existing_session_id or "-", user_name, len(initial_msg),
        )
        session_id, response_text, _ = gti_client.send_message(
            message=initial_msg, session_id=existing_session_id, files=attachments,
        )
        # Defense in depth: prompt.md instructs the model to never emit
        # an <at>...</at> mention tag, but that's a prompt-level
        # instruction, not a guarantee — prompt injection could still
        # induce one. Stripped here, before any downstream use, so it's
        # covered whether the response ends up as a native Adaptive Card
        # or the markdown fallback wrapper.
        response_text = strip_mentions(response_text)
        bind_request(session_id=session_id)
        if session_key:
            set_session_id(team_id, channel_id, session_key, session_id)
        logger.info(
            "[WORKER 4/5] GTI query completed in %.2fs | session_id=%s response_chars=%d",
            time.perf_counter() - t_gti, session_id, len(response_text),
        )

        # ── Step 5: Format & Deliver ─────────────────────────────────────────
        t_deliver = time.perf_counter()
        parsed_card, fallback_text = parse_adaptive_card(response_text)
        if parsed_card:
            logger.info("[WORKER 5/5] Parsed native Adaptive Card from GTI Agent response")
            card = inject_quote_into_card(parsed_card, quoted_query)
        else:
            logger.info("[WORKER 5/5] Formatting markdown into Adaptive Card wrapper")
            card = build_gti_response_card(response_text, quoted_query=quoted_query)

        fallback_text = f"{quoted_query}\n\n{fallback_text}" if quoted_query else fallback_text

        deliver_mode = "edit-in-place" if (loading_activity_id and scope == "channel") else (
            "delete-and-repost" if loading_activity_id else "fresh-send"
        )
        delivered = deliver_message(ctx, loading_activity_id, fallback_text, card, edit_in_place=(scope == "channel"))
        logger.info(
            "[WORKER 5/5] Response delivered in %.0fms | mode=%s is_native_card=%s success=%s",
            (time.perf_counter() - t_deliver) * 1000, deliver_mode, bool(parsed_card), delivered,
        )

        total_elapsed = time.perf_counter() - t_start
        if delivered:
            logger.info(
                "[WORKER DONE] Job finished successfully in %.2fs | user='%s' query='%s' status=delivered",
                total_elapsed, user_name, user_text, extra={"status": "delivered"},
            )
        else:
            logger.error(
                "[WORKER DONE] Delivery failed after %.2fs | user='%s' query='%s' status=failed",
                total_elapsed, user_name, user_text, extra={"status": "failed"},
            )

    except GTIAuthenticationError as exc:
        logger.error(
            "[WORKER ERROR] GTI API key authentication failed after %.2fs: %s | user='%s' query='%s'",
            time.perf_counter() - t_start, exc, user_name, user_text,
        )
        err_msg = "🔑 **Authentication Failed**\n\nThe Google Threat Intelligence API key is invalid or unauthorized. Please verify your `GTI_API_KEY` configuration."
        deliver_message(
            ctx, loading_activity_id,
            f"{quoted_query}\n\n{err_msg}" if quoted_query else err_msg,
            build_status_card(err_msg, quoted_query),
            edit_in_place=(scope == "channel"),
        )

    except GTIRateLimitError as exc:
        logger.error(
            "[WORKER ERROR] GTI rate limit exceeded after %.2fs: %s | user='%s' query='%s'",
            time.perf_counter() - t_start, exc, user_name, user_text,
        )
        err_msg = "⚠️ **Rate Limit Exceeded**\n\nThe Google Threat Intelligence API rate limit or quota has been reached. Please try again in a moment."
        deliver_message(
            ctx, loading_activity_id,
            f"{quoted_query}\n\n{err_msg}" if quoted_query else err_msg,
            build_status_card(err_msg, quoted_query),
            edit_in_place=(scope == "channel"),
        )

    except GTITimeoutError as exc:
        logger.error(
            "[WORKER ERROR] GTI request timed out after %.2fs: %s | user='%s' query='%s'",
            time.perf_counter() - t_start, exc, user_name, user_text,
        )
        err_msg = "⏱️ **Request Timed Out**\n\nThe threat intelligence query took too long to complete. Try asking a more specific question or query."
        deliver_message(
            ctx, loading_activity_id,
            f"{quoted_query}\n\n{err_msg}" if quoted_query else err_msg,
            build_status_card(err_msg, quoted_query),
            edit_in_place=(scope == "channel"),
        )

    except GTIServiceError as exc:
        logger.error(
            "[WORKER ERROR] GTI service unavailable after %.2fs: %s | user='%s' query='%s'",
            time.perf_counter() - t_start, exc, user_name, user_text,
        )
        err_msg = "⚠️ **Threat Intelligence Service Unavailable**\n\nThe Google Threat Intelligence service is temporarily unreachable. Please try again shortly."
        deliver_message(
            ctx, loading_activity_id,
            f"{quoted_query}\n\n{err_msg}" if quoted_query else err_msg,
            build_status_card(err_msg, quoted_query),
            edit_in_place=(scope == "channel"),
        )

    except GTIPayloadTooLargeError as exc:
        logger.error(
            "[WORKER ERROR] GTI rejected the request — payload too large after %.2fs: %s | user='%s' query='%s'",
            time.perf_counter() - t_start, exc, user_name, user_text,
        )
        err_msg = (
            "📁 **File Too Large**\n\n"
            "The attached file(s) exceed the maximum size the Google Threat Intelligence "
            "service accepts. Please upload a file less than 32 MB, or fewer files at once."
        )
        deliver_message(
            ctx, loading_activity_id,
            f"{quoted_query}\n\n{err_msg}" if quoted_query else err_msg,
            build_status_card(err_msg, quoted_query),
            edit_in_place=(scope == "channel"),
        )

    except GTIClientError as exc:
        logger.error(
            "[WORKER ERROR] GTI rejected the request after %.2fs: %s | user='%s' query='%s'",
            time.perf_counter() - t_start, exc, user_name, user_text,
        )
        err_msg = "🚫 **Request Rejected**\n\nThe Google Threat Intelligence service could not process this query. Try rephrasing your question."
        deliver_message(
            ctx, loading_activity_id,
            f"{quoted_query}\n\n{err_msg}" if quoted_query else err_msg,
            build_status_card(err_msg, quoted_query),
            edit_in_place=(scope == "channel"),
        )

    except GTIEmptyResponseError as exc:
        # GTI answered 200 OK but produced no usable result (e.g. a blocked
        # or failed generation on its own side) — a GTI-API-level failure,
        # not an HTTP/transport error. Not auto-retried: the same query would
        # most likely produce the same empty result again.
        logger.error(
            "[WORKER ERROR] GTI completed the request but returned no displayable result after %.2fs: %s | user='%s' query='%s'",
            time.perf_counter() - t_start, exc, user_name, user_text,
        )
        err_msg = "🤔 **No Results Found**\n\nNo threat intelligence results were returned for this query. Try rephrasing your question or providing more details."
        deliver_message(
            ctx, loading_activity_id,
            f"{quoted_query}\n\n{err_msg}" if quoted_query else err_msg,
            build_status_card(err_msg, quoted_query),
            edit_in_place=(scope == "channel"),
        )

    except Exception:
        logger.exception(
            "[WORKER ERROR] Unexpected error in GTI message handler after %.2fs | user='%s' query='%s'",
            time.perf_counter() - t_start, user_name, user_text,
        )
        err_msg = "⚠️ **Something went wrong while processing your request.** Please try again."
        deliver_message(
            ctx, loading_activity_id,
            f"{quoted_query}\n\n{err_msg}" if quoted_query else err_msg,
            build_status_card(err_msg, quoted_query),
            edit_in_place=(scope == "channel"),
        )


def handle_installation_removed(activity) -> None:
    """Delete stored session data for a team when the Teams app is uninstalled from it."""
    team_id = get_team_id(activity)
    if not team_id:
        channel_id = get_channel_id(activity)
        team_id = get_team_id_for_channel(channel_id) if channel_id else ""
    if not team_id:
        return
    logger.info("[EVENT] App removed from team=%s — deleting stored sessions.", team_id)
    delete_team_sessions(team_id)
