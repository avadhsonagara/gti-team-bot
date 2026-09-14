"""
Processes one dequeued GTI query job end-to-end: fetch channel thread
context, run the GTI Agentic Sessions API pipeline, and deliver the final
Adaptive Card response — editing (channel) or deleting-and-reposting
(personal/group) the placeholder the Ingest Function already posted.

This is bot-worker-function's equivalent of azure/azure-bot-function's
app/teams/handlers.py::_handle_user_query(), split apart because in this
queue architecture the placeholder was already sent by a different Function
App (bot-ingest-function/) before this job ever reached the queue.
"""
import logging
import re
from datetime import datetime, timezone

from app.config import settings
from app.constants import SYSTEM_PROMPT
from app.gti.client import (
    GTIAuthenticationError,
    GTIClientError,
    GTIEmptyResponseError,
    GTIPayloadTooLargeError,
    GTIRateLimitError,
    GTIServiceError,
    GTISessionNotFoundError,
    GTITimeoutError,
    gti_client,
)
from app.gti.session_store import get_session_id, set_session_id
from app.observability import bind_request
from app.output_format_store import get_output_format
from app.queue_job import parse_job_payload
from app.teams.activity import parse_activity
from app.teams.attachments import download_attachments
from app.teams.cards import build_gti_response_card, build_status_card, inject_quote_into_card
from app.teams.context import Ctx
from app.teams.thread import get_channel_id, get_session_key, get_team_id, get_thread_context
from app.utils.helpers import (
    build_custom_format_section,
    build_thread_context_section,
    deliver_message,
    parse_adaptive_card,
    strip_mentions,
)

logger = logging.getLogger("gti-teams-bot")

_STALE_JOB_NOTICE = (
    "⏱️ **Sorry, this took too long to get to.**\n\n"
    "There was a large backlog of requests ahead of yours. Please ask your question again."
)


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


def _get_tenant_id(activity) -> str:
    channel_data = getattr(activity, "channel_data", None) or {}
    if isinstance(channel_data, dict):
        tenant = channel_data.get("tenant") or {}
        if isinstance(tenant, dict) and tenant.get("id"):
            return tenant["id"]
    return getattr(activity.conversation, "tenant_id", "") or ""


def _quoted_query(user_text: str, scope: str) -> str:
    """
    Must reproduce exactly what bot-ingest-function/function_app.py computed
    for the SAME message when it built the placeholder text, so the
    placeholder and the final delivered response show the same quote.
    """
    if scope == "channel":
        return ""
    quote_lines = [f"> {line}" for line in user_text.splitlines()] or ["> "]
    return "\n".join(quote_lines)


def process_job(raw_payload: dict, dequeue_count: int = 1) -> None:
    activity_body, loading_activity_id, enqueued_at = parse_job_payload(raw_payload)
    activity = parse_activity(activity_body)
    ctx = Ctx(activity)

    conversation_id = activity.conversation.id
    scope = getattr(activity.conversation, "conversation_type", "") or ""
    sender = getattr(activity, "from_", None)
    user_id = getattr(sender, "id", "unknown") if sender else "unknown"
    tenant_id = _get_tenant_id(activity)

    bind_request(user=user_id, conversation=conversation_id, activity_id=activity.id or "")
    if tenant_id:
        bind_request(tenant=tenant_id)

    age_seconds = (datetime.now(timezone.utc) - enqueued_at).total_seconds()
    if age_seconds > settings.max_job_age_seconds:
        logger.warning(
            "[JOB] Job is stale (%.0fs old, limit %.0fs, dequeue_count=%d) — notifying user instead of querying GTI.",
            age_seconds, settings.max_job_age_seconds, dequeue_count,
        )
        deliver_message(
            ctx, loading_activity_id, _STALE_JOB_NOTICE, build_status_card(_STALE_JOB_NOTICE),
            edit_in_place=(scope == "channel"),
        )
        return

    # Must mirror bot-ingest-function/function_app.py's own _strip_mentions +
    # .strip() exactly: that function already computed a mention-stripped
    # user_text once to decide whether to enqueue at all and to build the
    # placeholder's quoted_query. Re-deriving it here from the same raw
    # activity.text without stripping mentions would (a) send the GTI prompt
    # an unstripped "<at>Bot Name</at> ..." query, and (b) show a quoted_query
    # in the final response that doesn't match what the placeholder quoted.
    user_text = strip_mentions(activity.text or "").strip()
    # Ingest already stripped mentions and rejected empty queries before ever
    # enqueueing — this is just a defensive backstop, not the primary check.
    if not user_text or not re.search(r"\w", user_text, re.UNICODE):
        logger.warning("[JOB] Dequeued job has no meaningful query text — dropping.")
        return

    quoted_query = _quoted_query(user_text, scope)
    attachments = download_attachments(ctx)

    try:
        preview = user_text[:80] + ("..." if len(user_text) > 80 else "")
        logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        logger.info("[EVENT] conversation=%s scope=%s dequeue_count=%d age=%.0fs", conversation_id, scope, dequeue_count, age_seconds)
        logger.info("[EVENT] query=%r attachments=%d", preview, len(attachments))

        # The except branches below deliberately do NOT re-raise for any
        # named GTIError subclass — those are known, already-handled failure
        # modes (the user gets a specific error card), so the queue message
        # is considered successfully processed and is never retried/poisoned
        # over them. Only the final bare `except Exception` re-raises, for
        # anything unanticipated — see its own comment below for why.

        # ── Step 1: Fetch channel thread context ──────────────────────────
        # Excludes this bot's own placeholder message (already posted by
        # bot-ingest-function/) via app/teams/thread.py::is_placeholder_message.
        thread_context = get_thread_context(activity, scope)

        # ── Step 2: Query GTI Agentic Sessions API (create or continue) ───
        output_format = get_output_format(settings)
        if thread_context:
            logger.info("[THREAD] Injecting channel thread context into prompt:\n%s", thread_context)

        initial_msg = _render_system_prompt(
            user_query=user_text, thread_context=thread_context, output_format=output_format,
        )

        if scope == "channel":
            session_key = get_session_key(activity, scope)
            team_id = get_team_id(activity)
            channel_id = get_channel_id(activity)
            existing_session_id = get_session_id(session_key)
        else:
            session_key = ""
            team_id = ""
            channel_id = ""
            existing_session_id = None
        logger.info(
            "[AGENTIC] Dispatching query to GTI Agentic API | conversation=%s session_key=%s team_id=%s mode=%s",
            conversation_id, session_key or "-", team_id or "-", "continue" if existing_session_id else "new",
        )
        session_id, response_text, _ = gti_client.send_message(
            message=initial_msg, session_id=existing_session_id, files=attachments,
        )
        bind_request(session_id=session_id)
        if session_key:
            set_session_id(session_key, session_id, team_id or None, channel_id or None)

        # ── Step 3: Format & Deliver ────────────────────────────────────────
        parsed_card, fallback_text = parse_adaptive_card(response_text)
        if parsed_card:
            logger.info("[PARSE] Successfully parsed native Adaptive Card from GTI Agent")
            card = inject_quote_into_card(parsed_card, quoted_query)
        else:
            logger.info("[PARSE] Using markdown Adaptive Card wrapper")
            card = build_gti_response_card(response_text, quoted_query=quoted_query)

        fallback_text = f"{quoted_query}\n\n{fallback_text}" if quoted_query else fallback_text

        deliver_mode = "edit-in-place" if (loading_activity_id and scope == "channel") else (
            "delete-and-repost" if loading_activity_id else "fresh-send"
        )
        logger.info(
            "[DELIVER] Sending response | length=%d chars mode=%s is_native_card=%s",
            len(response_text), deliver_mode, bool(parsed_card),
        )

        delivered = deliver_message(ctx, loading_activity_id, fallback_text, card, edit_in_place=(scope == "channel"))
        if delivered:
            logger.info("[DONE] Response delivered successfully.", extra={"status": "delivered"})
        else:
            logger.error("[DONE] All delivery attempts failed.", extra={"status": "failed"})

    except GTIAuthenticationError as exc:
        logger.error("[ERROR] GTI API key authentication failed: %s", exc)
        err_msg = "🔑 **Authentication Failed**\n\nThe Google Threat Intelligence API key is invalid or unauthorized. Please verify your `GTI_API_KEY` configuration."
        deliver_message(ctx, loading_activity_id, err_msg, build_status_card(err_msg), edit_in_place=(scope == "channel"))

    except GTIRateLimitError as exc:
        logger.error("[ERROR] GTI rate limit exceeded: %s", exc)
        err_msg = "⚠️ **Rate Limit Exceeded**\n\nThe Google Threat Intelligence API rate limit or quota has been reached. Please try again in a moment."
        deliver_message(ctx, loading_activity_id, err_msg, build_status_card(err_msg), edit_in_place=(scope == "channel"))

    except GTITimeoutError as exc:
        logger.error("[ERROR] GTI request timed out: %s", exc)
        err_msg = "⏱️ **Request Timed Out**\n\nThe threat intelligence query took too long to complete. Try asking a more specific question or query."
        deliver_message(ctx, loading_activity_id, err_msg, build_status_card(err_msg), edit_in_place=(scope == "channel"))

    except GTIServiceError as exc:
        logger.error("[ERROR] GTI service unavailable: %s", exc)
        err_msg = "⚠️ **Threat Intelligence Service Unavailable**\n\nThe Google Threat Intelligence service is temporarily unreachable. Please try again shortly."
        deliver_message(ctx, loading_activity_id, err_msg, build_status_card(err_msg), edit_in_place=(scope == "channel"))

    except GTISessionNotFoundError as exc:
        logger.error("[ERROR] GTI session not found or expired: %s", exc)
        err_msg = "🔄 **Session Expired**\n\nYour conversation session with the Google Threat Intelligence service has expired. Please start a new query."
        deliver_message(ctx, loading_activity_id, err_msg, build_status_card(err_msg), edit_in_place=(scope == "channel"))

    except GTIPayloadTooLargeError as exc:
        logger.error("[ERROR] GTI rejected the request — payload too large: %s", exc)
        err_msg = (
            "📁 **File Too Large**\n\n"
            "The attached file(s) exceed the maximum size the Google Threat Intelligence "
            "service accepts. Please upload a smaller file, or fewer files at once."
        )
        deliver_message(ctx, loading_activity_id, err_msg, build_status_card(err_msg), edit_in_place=(scope == "channel"))

    except GTIClientError as exc:
        logger.error("[ERROR] GTI rejected the request: %s", exc)
        err_msg = "🚫 **Request Rejected**\n\nThe Google Threat Intelligence service could not process this query. Try rephrasing your question."
        deliver_message(ctx, loading_activity_id, err_msg, build_status_card(err_msg), edit_in_place=(scope == "channel"))

    except GTIEmptyResponseError as exc:
        # GTI answered 200 OK but produced no usable result (e.g. a blocked
        # or failed generation on its own side) — a GTI-API-level failure,
        # not an HTTP/transport error. Not auto-retried: the same query would
        # most likely produce the same empty result again.
        logger.error("[ERROR] GTI completed the request but returned no displayable result: %s", exc)
        err_msg = "🤔 **No Result Produced**\n\nThe Google Threat Intelligence agent completed the request but didn't return a usable result. Try rephrasing your question."
        deliver_message(ctx, loading_activity_id, err_msg, build_status_card(err_msg), edit_in_place=(scope == "channel"))

    except Exception:
        # Unlike azure/azure-bot-function's handlers.py (which swallows this
        # to always ack 200 to Bot Framework), this MUST re-raise: the queue
        # trigger's own return value is what tells the Functions runtime
        # whether to retry (per host.json's maxDequeueCount) or leave the
        # message alone. Swallowing it here would silently drop a job that
        # deserved a retry. The user still sees a friendly card either way —
        # this only decides whether the platform also retries.
        logger.exception("[ERROR] Unexpected error in GTI job processor.")
        err_msg = "⚠️ **Something went wrong while processing your request.** Please try again."
        deliver_message(ctx, loading_activity_id, err_msg, build_status_card(err_msg), edit_in_place=(scope == "channel"))
        raise

    finally:
        logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
