"""
Azure Functions (Python v2 programming model) entry point — Worker Function.

Triggered by the Storage Queue bot-ingest-function/ enqueues to
(settings.job_queue_name): runs the actual GTI Agentic API query — which can
take up to gti_timeout_seconds — and delivers the final response by editing
(channel) or deleting-and-reposting (personal/group) the placeholder the
Ingest Function already posted. See app/job_processor.py for the full
pipeline.

Also declares the matching poison-queue handler: the Azure Functions Storage
extension automatically routes a message here after
extensions.queues.maxDequeueCount (host.json) failed attempts, into a queue
named "<job_queue_name>-poison" — this app is the only place either queue
needs consuming, so both triggers live in this one Function App.

Requires host.json's extensionBundle (unlike bot-ingest-function, which
doesn't use any native binding) — the queue_trigger decorator is what gives
this app automatic per-message retry, lease renewal while an invocation is
alive, and poison-queue routing; a hand-rolled polling loop over the SDK
would have to reimplement all three.
"""
import json
import logging

import azure.functions as func

from app.config import settings
from app.installation_handler import process_installation_removed
from app.job_processor import process_job
from app.logging_config import setup_logging
from app.observability import clear_request
from app.poison_handler import process_poison_job
from app.queue_job import InvalidJobPayload, get_job_kind

setup_logging()

logger = logging.getLogger("gti-teams-bot")

logger.info(
    "GTI Teams Bot Worker Function starting | gti_url=%s gti_key=%s client_id=%s managed_identity=%s queue=%s",
    settings.gti_api_base_url,
    "set" if settings.gti_api_key else "MISSING",
    "set" if settings.client_id else "MISSING",
    "set" if settings.managed_identity_client_id else "MISSING",
    settings.job_queue_name,
)

app = func.FunctionApp()


@app.queue_trigger(arg_name="msg", queue_name=settings.job_queue_name, connection="AzureWebJobsStorage")
def process_query_job(msg: func.QueueMessage) -> None:
    """Main job queue trigger — processes one GTI query end-to-end."""
    try:
        raw = json.loads(msg.get_body().decode("utf-8"))
        kind = get_job_kind(raw)
        if kind == "installationUpdateRemove":
            logger.info("[QUEUE TRIGGER] Dequeued installation cleanup job | msg_id=%s dequeue_count=%d", msg.id, msg.dequeue_count)
            process_installation_removed(raw)
        else:
            if msg.dequeue_count > 1:
                logger.warning(
                    "[QUEUE TRIGGER RETRY] Dequeued retry attempt %d for job from %s | msg_id=%s",
                    msg.dequeue_count, settings.job_queue_name, msg.id,
                )
            else:
                logger.info("[QUEUE TRIGGER] Dequeued job from %s | msg_id=%s dequeue_count=%d", settings.job_queue_name, msg.id, msg.dequeue_count)
            process_job(raw, dequeue_count=msg.dequeue_count)
    except InvalidJobPayload:
        # A message that was never a valid job of ours (shouldn't happen —
        # only bot-ingest-function ever writes to this queue — but this is
        # cheap insurance). Re-raising lets it exhaust maxDequeueCount and
        # land in the poison queue rather than being silently dropped here.
        logger.exception("[JOB] Malformed job payload | msg_id=%s dequeue_count=%d", getattr(msg, "id", "-"), getattr(msg, "dequeue_count", 0))
        raise
    except Exception:
        logger.exception(
            "[JOB RETRY] Job attempt %d failed | msg_id=%s — will retry per host.json maxDequeueCount.",
            getattr(msg, "dequeue_count", 1), getattr(msg, "id", "-"),
        )
        raise
    finally:
        clear_request()


@app.queue_trigger(
    arg_name="msg",
    queue_name=f"{settings.job_queue_name}-poison",
    connection="AzureWebJobsStorage",
)
def process_poisoned_job(msg: func.QueueMessage) -> None:
    """
    A job that failed every retry lands here, auto-routed by the Functions
    runtime. Best-effort tells the user their request failed instead of
    leaving their placeholder stuck on "looking into that…" forever. Never
    raises — there's no further queue for this one to be routed to, and a
    raised exception here would just retry the poison handler itself.
    """
    try:
        logger.warning(
            "[POISON TRIGGER] Dequeued message from %s-poison | msg_id=%s dequeue_count=%d",
            settings.job_queue_name, msg.id, msg.dequeue_count,
        )
        raw = json.loads(msg.get_body().decode("utf-8"))
        process_poison_job(raw)
    except Exception:
        logger.exception("[POISON] Failed to notify user of a permanently failed job | msg_id=%s", getattr(msg, "id", "-"))
    finally:
        clear_request()
