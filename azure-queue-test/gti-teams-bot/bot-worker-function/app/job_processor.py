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
import time
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
from app.teams.thread import get_channel_id, get_team_id, get_team_post_id, get_thread_context
from app.utils.helpers import (
    build_custom_format_section,
    build_thread_context_section,
    deliver_message,
    parse_adaptive_card,
    strip_mentions,
)

logger = logging.getLogger("gti-teams-bot")


class DeliveryFailedError(Exception):
    """Raised when deliver_message() exhausts every delivery fallback."""


_STALE_JOB_NOTICE = (
    "⏱️ **Request Timed Out**\n\n"
    "Due to high activity, your request could not be processed in time. Please ask your question again."
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
    t_worker_start = time.perf_counter()
    activity_body, loading_activity_id, enqueued_at = parse_job_payload(raw_payload)
    activity = parse_activity(activity_body)
    ctx = Ctx(activity)

    conversation_id = activity.conversation.id
    scope = getattr(activity.conversation, "conversation_type", "") or ""
    sender = getattr(activity, "from_", None)
    user_id = getattr(sender, "id", "unknown") if sender else "unknown"
    tenant_id = _get_tenant_id(activity)

    # Bind request_id to activity.id so Application Insights correlates Ingest and Worker logs seamlessly
    bind_request(
        request_id=activity.id or "",
        user=user_id,
        conversation=conversation_id,
        activity_id=activity.id or "",
    )
    if tenant_id:
        bind_request(tenant=tenant_id)

    # Must mirror bot-ingest-function/function_app.py's own _strip_mentions +
    # .strip() exactly: that function already computed a mention-stripped
    # user_text once to decide whether to enqueue at all and to build the
    # placeholder's quoted_query. Re-deriving it here from the same raw
    # activity.text without stripping mentions would (a) send the GTI prompt
    # an unstripped "<at>Bot Name</at> ..." query, and (b) show a quoted_query
    # in the final response that doesn't match what the placeholder quoted.
    # Computed before the stale-job check below so that notice can also
    # carry the quote — every personal/group notice should, not just the
    # ones from the GTI* handlers further down.
    user_text = strip_mentions(activity.text or "").strip()
    quoted_query = _quoted_query(user_text, scope)

    age_seconds = (datetime.now(timezone.utc) - enqueued_at).total_seconds()
    logger.info(
        "[WORKER START] Dequeued job | activity_id=%s scope=%s dequeue_count=%d queue_wait=%.1fs",
        activity.id or "-", scope, dequeue_count, age_seconds,
    )

    if age_seconds > settings.max_job_age_seconds:
        logger.warning(
            "[WORKER] Job is stale (%.1fs old, limit %.0fs, dequeue_count=%d) — notifying user instead of querying GTI.",
            age_seconds, settings.max_job_age_seconds, dequeue_count,
        )
        deliver_message(
            ctx, loading_activity_id,
            f"{quoted_query}\n\n{_STALE_JOB_NOTICE}" if quoted_query else _STALE_JOB_NOTICE,
            build_status_card(_STALE_JOB_NOTICE, quoted_query),
            edit_in_place=(scope == "channel"),
        )
        return

    # Ingest already stripped mentions and rejected empty queries before ever
    # enqueueing — this is just a defensive backstop, not the primary check.
    if not user_text or not re.search(r"\w", user_text, re.UNICODE):
        logger.warning("[WORKER] Dequeued job has no meaningful query text — dropping.")
        return

    t_att = time.perf_counter()
    attachments = download_attachments(ctx)
    logger.info(
        "[WORKER 1/4] Attachments processed in %.0fms | count=%d",
        (time.perf_counter() - t_att) * 1000, len(attachments),
    )

    try:
        # Deliberately logs only lengths and counts — never the query text
        # itself, thread context, or GTI's response text.

        # ── Step 2: Fetch channel thread context ──────────────────────────
        # Excludes this bot's own placeholder message (already posted by
        # bot-ingest-function/) via app/teams/thread.py::is_placeholder_message.
        t_ctx = time.perf_counter()
        thread_context = get_thread_context(activity, scope)
        logger.info(
            "[WORKER 2/4] Thread context processed in %.0fms | active=%s chars=%d",
            (time.perf_counter() - t_ctx) * 1000, bool(thread_context), len(thread_context),
        )

        # ── Step 3: Query GTI Agentic Sessions API (create or continue) ───
        output_format = get_output_format(settings)
        initial_msg = _render_system_prompt(
            user_query=user_text, thread_context=thread_context, output_format=output_format,
        )

        # Session persistence (and therefore thread continuity) only applies
        # to channel messages — personal/group chats always start a fresh
        # GTI session per message, and nothing is ever written to
        # app/gti/session_store.py for those scopes.
        if scope == "channel":
            team_id = get_team_id(activity)
            channel_id = get_channel_id(activity)
            team_post_id = get_team_post_id(activity)
            have_session_key = bool(team_id and channel_id and team_post_id)
        else:
            team_id = channel_id = team_post_id = ""
            have_session_key = False

        existing_session_id = get_session_id(team_id, channel_id, team_post_id) if have_session_key else None

        t_gti = time.perf_counter()
        logger.info(
            "[WORKER 3/4] Dispatching query to GTI Agentic API | mode=%s session_id=%s prompt_chars=%d",
            "continue" if existing_session_id else "new", existing_session_id or "-", len(initial_msg),
        )
        session_id, response_text, _ = gti_client.send_message(
            message=initial_msg, session_id=existing_session_id, files=attachments,
        )
        bind_request(session_id=session_id)
        if have_session_key:
            set_session_id(team_id, channel_id, team_post_id, session_id)
        logger.info(
            "[WORKER 3/4] GTI query completed in %.2fs | session_id=%s response_chars=%d",
            time.perf_counter() - t_gti, session_id, len(response_text),
        )

        # ── Step 4: Format & Deliver ────────────────────────────────────────
        t_del = time.perf_counter()
        parsed_card, fallback_text = parse_adaptive_card(response_text)
        if parsed_card:
            logger.info("[WORKER 4/4] Parsed native Adaptive Card from GTI Agent")
            card = inject_quote_into_card(parsed_card, quoted_query)
        else:
            logger.info("[WORKER 4/4] Formatting markdown into Adaptive Card wrapper")
            card = build_gti_response_card(response_text, quoted_query=quoted_query)

        fallback_text = f"{quoted_query}\n\n{fallback_text}" if quoted_query else fallback_text

        deliver_mode = "edit-in-place" if (loading_activity_id and scope == "channel") else (
            "delete-and-repost" if loading_activity_id else "fresh-send"
        )
        delivered = deliver_message(ctx, loading_activity_id, fallback_text, card, edit_in_place=(scope == "channel"))
        logger.info(
            "[WORKER 4/4] Response delivered in %.0fms | mode=%s is_native_card=%s success=%s",
            (time.perf_counter() - t_del) * 1000, deliver_mode, bool(parsed_card), delivered,
        )

        total_elapsed = time.perf_counter() - t_worker_start
        if not delivered:
            # deliver_message() already exhausted every fallback (card, plain
            # text, generic notice, delete+resend) — this is a hard delivery
            # failure (e.g. Bot Framework outage), not something retrying
            # in-process would fix. Raising here — same as the generic
            # except Exception branch below — lets the queue's own
            # retry/poison mechanism take over instead of the runtime
            # treating this as success and deleting the message.
            logger.error("[WORKER DONE] Delivery failed after %.2fs", total_elapsed, extra={"status": "failed"})
            raise DeliveryFailedError(f"All delivery attempts failed for activity {activity.id}")
        logger.info("[WORKER DONE] Job finished successfully in %.2fs", total_elapsed, extra={"status": "delivered"})

    except GTIAuthenticationError as exc:
        logger.error("[WORKER ERROR] GTI API key authentication failed after %.2fs: %s", time.perf_counter() - t_worker_start, exc)
        err_msg = "🔑 **Service Unavailable**\n\nUnable to authenticate with the threat intelligence service. Please contact your bot administrator."
        deliver_message(
            ctx, loading_activity_id,
            f"{quoted_query}\n\n{err_msg}" if quoted_query else err_msg,
            build_status_card(err_msg, quoted_query),
            edit_in_place=(scope == "channel"),
        )

    except GTIRateLimitError as exc:
        logger.error("[WORKER ERROR] GTI rate limit exceeded after %.2fs: %s", time.perf_counter() - t_worker_start, exc)
        err_msg = "⚠️ **High Demand**\n\nThe service is currently experiencing high request volume. Please wait a moment and try your query again."
        deliver_message(
            ctx, loading_activity_id,
            f"{quoted_query}\n\n{err_msg}" if quoted_query else err_msg,
            build_status_card(err_msg, quoted_query),
            edit_in_place=(scope == "channel"),
        )

    except GTITimeoutError as exc:
        logger.error("[WORKER ERROR] GTI request timed out after %.2fs: %s", time.perf_counter() - t_worker_start, exc)
        err_msg = "⏱️ **Request Timed Out**\n\nThe query took too long to complete. Please try asking a more specific question or narrowing down your search."
        deliver_message(
            ctx, loading_activity_id,
            f"{quoted_query}\n\n{err_msg}" if quoted_query else err_msg,
            build_status_card(err_msg, quoted_query),
            edit_in_place=(scope == "channel"),
        )

    except GTIServiceError as exc:
        logger.error("[WORKER ERROR] GTI service unavailable after %.2fs: %s", time.perf_counter() - t_worker_start, exc)
        err_msg = "⚠️ **Service Temporarily Unavailable**\n\nThe threat intelligence service is currently unreachable. Please try again in a few moments."
        deliver_message(
            ctx, loading_activity_id,
            f"{quoted_query}\n\n{err_msg}" if quoted_query else err_msg,
            build_status_card(err_msg, quoted_query),
            edit_in_place=(scope == "channel"),
        )

    except GTISessionNotFoundError as exc:
        logger.error("[WORKER ERROR] GTI session not found or expired after %.2fs: %s", time.perf_counter() - t_worker_start, exc)
        err_msg = (
            "🔄 **Thread Session Expired**\n\n"
            "The conversation session for this channel thread has timed out. "
            "Please post your question again to start a fresh analysis."
        )
        deliver_message(
            ctx, loading_activity_id,
            f"{quoted_query}\n\n{err_msg}" if quoted_query else err_msg,
            build_status_card(err_msg, quoted_query),
            edit_in_place=(scope == "channel"),
        )

    except GTIPayloadTooLargeError as exc:
        logger.error("[WORKER ERROR] GTI rejected the request — payload too large after %.2fs: %s", time.perf_counter() - t_worker_start, exc)
        err_msg = (
            "📁 **File Too Large**\n\n"
            "The attached file(s) exceed the allowable upload size. Please try uploading a smaller file or fewer files at once."
        )
        deliver_message(
            ctx, loading_activity_id,
            f"{quoted_query}\n\n{err_msg}" if quoted_query else err_msg,
            build_status_card(err_msg, quoted_query),
            edit_in_place=(scope == "channel"),
        )

    except GTIClientError as exc:
        logger.error("[WORKER ERROR] GTI rejected the request after %.2fs: %s", time.perf_counter() - t_worker_start, exc)
        err_msg = "🚫 **Unable to Process Request**\n\nWe couldn't process this request. Please try rephrasing your question or checking your input."
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
        logger.error("[WORKER ERROR] GTI completed the request but returned no displayable result after %.2fs: %s", time.perf_counter() - t_worker_start, exc)
        err_msg = "🤔 **No Results Found**\n\nNo threat intelligence results were returned for this query. Try rephrasing your question or providing more details."
        deliver_message(
            ctx, loading_activity_id,
            f"{quoted_query}\n\n{err_msg}" if quoted_query else err_msg,
            build_status_card(err_msg, quoted_query),
            edit_in_place=(scope == "channel"),
        )

    except DeliveryFailedError:
        # Already logged above at the raise site. Deliberately does NOT
        # attempt another delivery here — every fallback deliver_message()
        # has already failed once this invocation, so retrying in-process
        # would just fail the same way. Re-raising lets the queue's own
        # retry (and eventual poison-queue routing) take over instead.
        raise

    except Exception:
        # Deliberately does NOT deliver an error card here (unlike every
        # named GTI* handler above, which are terminal — they never retry).
        # This branch WILL be retried once more, and for a personal/group
        # chat that means delete-and-repost: if this card were shown now and
        # the retry also failed, the user would end up with this card, then
        # a second copy of it from the retry, then a third, different card
        # from poison_handler.py once maxDequeueCount is exhausted. Silently
        # re-raising here means poison_handler.py is the ONLY place that
        # ever tells the user about a truly-failed (all retries exhausted)
        # unexpected error — exactly one message, not up to three.
        logger.exception("[WORKER FATAL] Unexpected error in GTI job processor after %.2fs — re-raising for queue retry.", time.perf_counter() - t_worker_start)
        raise
