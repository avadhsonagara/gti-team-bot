"""
Azure Functions entry point for the GTI Teams Bot Worker Function.

Processes background queue jobs dequeued from Azure Storage Queue, including
GTI queries, conversation cleanup events, and poison-queue failure notifications.
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
    """
    Process incoming queue messages from the primary job queue.

    Parses the message payload, inspects the job kind, and dispatches to
    either installation cleanup or GTI query processing.

    Args:
        msg: Azure Functions QueueMessage received from the job queue.
    """
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
    Process messages routed to the poison queue after exhausting retry attempts.

    Attempts to notify the user in Teams that their request could not be completed,
    cleaning up or replacing the pending placeholder message.

    Args:
        msg: Azure Functions QueueMessage received from the poison queue.
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
