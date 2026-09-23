"""
End-to-end job processor for GTI query execution.

Coordinates thread context retrieval, attachment downloading, GTI Agentic API
sessions, Adaptive Card response formatting, and delivery back to Microsoft Teams.

Retry signaling: unlike Azure's queue trigger (which retries on an unhandled
exception and stops on a normal return), Pub/Sub push retries on any non-2xx
HTTP response from the worker and stops on 2xx. This module keeps the same
exception taxonomy Azure's job_processor.py used — DeliveryFailedError and an
unexpected Exception are the only two cases meant to trigger redelivery,
every named GTI*Error is terminal (a friendly card is delivered and this
returns normally) — and leaves translating that into an HTTP status code to
main.py's /tasks/process handler, which is the only place that actually
knows about Pub/Sub.
"""
import logging
import re
import time
from datetime import datetime, timezone

from app.config import settings
from app.constants import SYSTEM_PROMPT
from app.dedup_store import claim_message
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


class DeliveryFailedError(Exception):
    """Raised when message delivery to Microsoft Teams fails after all fallback attempts."""


class DuplicateDeliveryError(Exception):
    """Raised when this Pub/Sub messageId has already been claimed by another delivery."""


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
    """Extract the Microsoft 365 tenant ID from an inbound activity."""
    channel_data = getattr(activity, "channel_data", None) or {}
    if isinstance(channel_data, dict):
        tenant = channel_data.get("tenant") or {}
        if isinstance(tenant, dict) and tenant.get("id"):
            return tenant["id"]
    return getattr(activity.conversation, "tenant_id", "") or ""


def _quoted_query(user_text: str, scope: str) -> str:
    """Markdown quote lines for the user query, shown above responses in personal/group chats."""
    if scope == "channel":
        return ""
    quote_lines = [f"> {line}" for line in user_text.splitlines()] or ["> "]
    return "\n".join(quote_lines)


def process_job(raw_payload: dict, message_id: str = "", delivery_attempt: int = 1) -> None:
    """
    Process a pushed query job end-to-end.

    Extracts activity details, claims the Pub/Sub messageId (guards against a
    concurrent redelivery landing on a second instance while this one is
    still working), checks job age, downloads attachments, fetches thread
    context, invokes the GTI Agentic API, formats the resulting card or
    text, and delivers the response to Microsoft Teams.

    Args:
        raw_payload: Decoded job dictionary (already unwrapped from Pub/Sub).
        message_id: The Pub/Sub messageId this job was delivered under.
        delivery_attempt: Pub/Sub's own delivery-attempt counter, present on
            the push request body once a dead-letter policy is configured —
            used only for logging, never for control flow.

    Raises:
        DeliveryFailedError: If Teams message delivery fails across all attempts.
        Exception: Re-raised on unexpected errors — main.py maps this to a
            5xx response so Pub/Sub's own retry/dead-letter policy takes over.
    """
    t_worker_start = time.perf_counter()
    activity_body, loading_activity_id, enqueued_at = parse_job_payload(raw_payload)
    activity = parse_activity(activity_body)
    ctx = Ctx(activity)

    conversation_id = activity.conversation.id
    scope = getattr(activity.conversation, "conversation_type", "") or ""
    sender = getattr(activity, "from_", None)
    user_id = getattr(sender, "id", "unknown") if sender else "unknown"
    user_name = (getattr(sender, "name", None) or user_id) if sender else "unknown"
    tenant_id = _get_tenant_id(activity)

    user_text = strip_mentions(activity.text or "").strip()
    quoted_query = _quoted_query(user_text, scope)
    age_seconds = (datetime.now(timezone.utc) - enqueued_at).total_seconds()

    bind_request(
        request_id=activity.id or "",
        user=user_id,
        user_name=user_name,
        query=user_text,
        scope=scope,
        conversation=conversation_id,
        activity_id=activity.id or "",
    )
    if tenant_id:
        bind_request(tenant=tenant_id)

    if delivery_attempt > 1:
        logger.warning(
            "[WORKER RETRY] Redelivery attempt %d | user='%s' (%s) scope=%s | query='%s' | queue_wait=%.1fs",
            delivery_attempt, user_name, user_id, scope, user_text, age_seconds,
        )
    else:
        logger.info(
            "[WORKER START] Processing User Query | user='%s' (%s) scope=%s | query='%s' | delivery_attempt=%d queue_wait=%.1fs",
            user_name, user_id, scope, user_text, delivery_attempt, age_seconds,
        )

    if not claim_message(message_id):
        # Another delivery (this instance or a concurrent one) is already
        # processing this exact messageId — most likely Pub/Sub redelivered
        # before the first attempt finished within ack_deadline_seconds.
        # Deliberately does NOT touch the placeholder or notify the user:
        # the delivery that holds the claim will do that. Raising (rather
        # than returning) so main.py acks this one with 200 anyway — see
        # its DuplicateDeliveryError handling.
        raise DuplicateDeliveryError(f"messageId={message_id} already claimed by another delivery")

    if age_seconds > settings.max_job_age_seconds:
        logger.warning(
            "[WORKER] Job is stale (%.1fs old, limit %.0fs, delivery_attempt=%d) | user='%s' query='%s' — notifying user instead of querying GTI.",
            age_seconds, settings.max_job_age_seconds, delivery_attempt, user_name, user_text,
        )
        deliver_message(
            ctx, loading_activity_id,
            f"{quoted_query}\n\n{_STALE_JOB_NOTICE}" if quoted_query else _STALE_JOB_NOTICE,
            build_status_card(_STALE_JOB_NOTICE, quoted_query),
            edit_in_place=(scope == "channel"),
        )
        return

    # Ingest already stripped mentions and rejected empty queries before ever
    # publishing — this is just a defensive backstop, not the primary check.
    if not user_text or not re.search(r"\w", user_text, re.UNICODE):
        logger.warning("[WORKER] Pushed job has no meaningful query text | user='%s' — dropping.", user_name)
        return

    t_att = time.perf_counter()
    attachments = download_attachments(ctx)
    att_summary = f"count={len(attachments)}"
    if attachments:
        att_names = []
        for a in attachments:
            if isinstance(a, (list, tuple)) and len(a) > 0 and a[0]:
                att_names.append(str(a[0]))
            elif isinstance(a, dict) and a.get("name"):
                att_names.append(str(a.get("name")))
        if att_names:
            att_summary += f" ({', '.join(att_names)})"
    logger.info(
        "[WORKER 1/4] Attachments processed in %.0fms | %s",
        (time.perf_counter() - t_att) * 1000, att_summary,
    )

    try:
        # ── Step 2: Fetch channel thread context ──────────────────────────
        # Excludes this bot's own placeholder message (already posted by
        # bot-ingest-function) via app/teams/thread.py::is_placeholder_message.
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
            session_key = get_session_key(activity, scope)
            have_session_key = bool(team_id and channel_id and session_key)
        else:
            team_id = channel_id = session_key = ""
            have_session_key = False

        existing_session_id = get_session_id(team_id, channel_id, session_key) if have_session_key else None

        t_gti = time.perf_counter()
        logger.info(
            "[WORKER 3/4] Dispatching query to GTI Agentic API | mode=%s session_id=%s user='%s' prompt_chars=%d",
            "continue" if existing_session_id else "new", existing_session_id or "-", user_name, len(initial_msg),
        )
        session_id, response_text, _ = gti_client.send_message(
            message=initial_msg, session_id=existing_session_id, files=attachments,
        )
        # Defense in depth: prompt.md instructs the model to never emit an
        # <at>...</at> mention tag, but that's a prompt-level instruction,
        # not a guarantee — prompt injection could still induce one.
        # Stripped here, before any downstream use, so it's covered whether
        # the response ends up as a native Adaptive Card or the markdown
        # fallback wrapper.
        response_text = strip_mentions(response_text)
        bind_request(session_id=session_id)
        if have_session_key:
            set_session_id(team_id, channel_id, session_key, session_id)
        logger.info(
            "[WORKER 3/4] GTI query completed in %.2fs | session_id=%s response_chars=%d",
            time.perf_counter() - t_gti, session_id, len(response_text),
        )

        # ── Step 4: Format & Deliver ────────────────────────────────────────
        t_del = time.perf_counter()
        parsed_card, fallback_text = parse_adaptive_card(response_text)
        if parsed_card:
            logger.info("[WORKER 4/4] Parsed native Adaptive Card from GTI Agent response")
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
            # except Exception branch below — lets main.py return a 5xx so
            # Pub/Sub's own retry/dead-letter policy takes over instead of
            # this being acked as a success.
            logger.error(
                "[WORKER DONE] Delivery failed after %.2fs | user='%s' query='%s' status=failed",
                total_elapsed, user_name, user_text, extra={"status": "failed"},
            )
            raise DeliveryFailedError(f"All delivery attempts failed for activity {activity.id}")
        logger.info(
            "[WORKER DONE] Job finished successfully in %.2fs | user='%s' query='%s' status=delivered",
            total_elapsed, user_name, user_text, extra={"status": "delivered"},
        )

    except GTIAuthenticationError as exc:
        logger.error(
            "[WORKER ERROR] GTI API key authentication failed after %.2fs: %s | user='%s' query='%s'",
            time.perf_counter() - t_worker_start, exc, user_name, user_text,
        )
        err_msg = "🔑 **Service Unavailable**\n\nUnable to authenticate with the threat intelligence service. Please contact your bot administrator."
        deliver_message(
            ctx, loading_activity_id,
            f"{quoted_query}\n\n{err_msg}" if quoted_query else err_msg,
            build_status_card(err_msg, quoted_query),
            edit_in_place=(scope == "channel"),
        )

    except GTIRateLimitError as exc:
        logger.error(
            "[WORKER ERROR] GTI rate limit exceeded after %.2fs: %s | user='%s' query='%s'",
            time.perf_counter() - t_worker_start, exc, user_name, user_text,
        )
        err_msg = "⚠️ **High Demand**\n\nThe service is currently experiencing high request volume. Please wait a moment and try your query again."
        deliver_message(
            ctx, loading_activity_id,
            f"{quoted_query}\n\n{err_msg}" if quoted_query else err_msg,
            build_status_card(err_msg, quoted_query),
            edit_in_place=(scope == "channel"),
        )

    except GTITimeoutError as exc:
        logger.error(
            "[WORKER ERROR] GTI request timed out after %.2fs: %s | user='%s' query='%s'",
            time.perf_counter() - t_worker_start, exc, user_name, user_text,
        )
        err_msg = "⏱️ **Request Timed Out**\n\nThe query took too long to complete. Please try asking a more specific question or narrowing down your search."
        deliver_message(
            ctx, loading_activity_id,
            f"{quoted_query}\n\n{err_msg}" if quoted_query else err_msg,
            build_status_card(err_msg, quoted_query),
            edit_in_place=(scope == "channel"),
        )

    except GTIServiceError as exc:
        logger.error(
            "[WORKER ERROR] GTI service unavailable after %.2fs: %s | user='%s' query='%s'",
            time.perf_counter() - t_worker_start, exc, user_name, user_text,
        )
        err_msg = "⚠️ **Service Temporarily Unavailable**\n\nThe threat intelligence service is currently unreachable. Please try again in a few moments."
        deliver_message(
            ctx, loading_activity_id,
            f"{quoted_query}\n\n{err_msg}" if quoted_query else err_msg,
            build_status_card(err_msg, quoted_query),
            edit_in_place=(scope == "channel"),
        )

    except GTIPayloadTooLargeError as exc:
        logger.error(
            "[WORKER ERROR] GTI rejected the request — payload too large after %.2fs: %s | user='%s' query='%s'",
            time.perf_counter() - t_worker_start, exc, user_name, user_text,
        )
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
        logger.error(
            "[WORKER ERROR] GTI rejected the request after %.2fs: %s | user='%s' query='%s'",
            time.perf_counter() - t_worker_start, exc, user_name, user_text,
        )
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
        logger.error(
            "[WORKER ERROR] GTI completed the request but returned no displayable result after %.2fs: %s | user='%s' query='%s'",
            time.perf_counter() - t_worker_start, exc, user_name, user_text,
        )
        err_msg = "🤔 **No Results Found**\n\nNo threat intelligence results were returned for this query. Try rephrasing your question or providing more details."
        deliver_message(
            ctx, loading_activity_id,
            f"{quoted_query}\n\n{err_msg}" if quoted_query else err_msg,
            build_status_card(err_msg, quoted_query),
            edit_in_place=(scope == "channel"),
        )

    except DeliveryFailedError as exc:
        # Already logged above at the raise site. Deliberately does NOT
        # attempt another delivery here — every fallback deliver_message()
        # has already failed once this invocation, so retrying in-process
        # would just fail the same way. Re-raising lets main.py return a
        # 5xx so Pub/Sub's own retry (and eventual dead-letter routing)
        # takes over instead.
        logger.warning(
            "[WORKER RETRY SCHEDULED] Delivery failed (attempt %d) — raising for Pub/Sub redelivery | user='%s' query='%s': %s",
            delivery_attempt, user_name, user_text, exc,
        )
        raise

    except Exception as exc:
        # Deliberately does NOT deliver an error card here (unlike every
        # named GTI* handler above, which are terminal — they never retry).
        # This branch WILL be retried, and for a personal/group chat that
        # means delete-and-repost: if this card were shown now and the
        # retry also failed, the user would end up with this card, then a
        # second copy of it from the retry, then a third, different card
        # from poison_handler.py once max_delivery_attempts is exhausted.
        # Silently re-raising here means poison_handler.py is the ONLY
        # place that ever tells the user about a truly-failed (all retries
        # exhausted) unexpected error — exactly one message, not several.
        logger.exception(
            "[WORKER RETRY SCHEDULED] Unexpected error in GTI job processor (attempt %d) after %.2fs | user='%s' query='%s' — re-raising for Pub/Sub redelivery: %s",
            delivery_attempt, time.perf_counter() - t_worker_start, user_name, user_text, exc,
        )
        raise
