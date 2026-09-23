"""
Google Cloud Run function (2nd Gen) entrypoint for the GTI Teams Bot Worker.

Pub/Sub push targets — NOT public; Cloud Run invoker IAM on this function is
restricted to the push identity service account terraform/main.tf creates
(see terraform/main.tf's google_cloud_run_v2_service_iam_member.worker_invoker).

  POST /tasks/process  — pushed from the main job subscription.
  POST /tasks/poison   — pushed from the dead-letter subscription, after
                          Pub/Sub has exhausted max_delivery_attempts on the
                          main subscription.

HTTP status code IS the retry signal Pub/Sub acts on: any non-2xx response
from /tasks/process causes redelivery (per the subscription's retry_policy)
up to max_delivery_attempts, after which Pub/Sub routes the message to the
dead-letter topic instead of this worker retrying it. See
app/job_processor.py's module docstring for the full exception -> status
code mapping this handler implements.
"""
import logging

import functions_framework
from flask import Request, jsonify

from app.config import settings
from app.installation_handler import process_installation_removed
from app.job_processor import DuplicateDeliveryError, process_job
from app.logging_config import setup_logging
from app.observability import clear_request
from app.poison_handler import process_poison_job
from app.queue_job import InvalidJobPayload, get_job_kind, unwrap_pubsub_envelope

setup_logging()

logger = logging.getLogger("gti-teams-bot")

logger.info(
    "GTI Teams Bot Worker Function (GCP) starting | gti_url=%s gti_key=%s client_id=%s",
    settings.gti_api_base_url,
    "set" if settings.gti_api_key else "MISSING",
    "set" if settings.client_id else "MISSING",
)


def _handle_process(request: Request):
    """Handle a push delivery from the main job subscription."""
    envelope = request.get_json(silent=True)
    try:
        raw_payload, message_id = unwrap_pubsub_envelope(envelope)
    except InvalidJobPayload:
        # Malformed push envelope/payload — will fail identically on every
        # redelivery, so this is a 5xx (not 400): let Pub/Sub's own
        # retry-then-dead-letter policy take over rather than acking and
        # silently dropping a message with no dead-letter visibility.
        logger.exception("[WORKER] Malformed push envelope — returning 500 for Pub/Sub redelivery.")
        return "", 500

    # envelope is a dict at this point — unwrap_pubsub_envelope() above
    # would have raised InvalidJobPayload (and returned 500) otherwise.
    delivery_attempt = envelope.get("deliveryAttempt", 1)

    try:
        if get_job_kind(raw_payload) == "installationUpdateRemove":
            logger.info("[WORKER] Pushed installation cleanup job | messageId=%s delivery_attempt=%d", message_id, delivery_attempt)
            process_installation_removed(raw_payload)
        else:
            process_job(raw_payload, message_id=message_id, delivery_attempt=delivery_attempt)
    except DuplicateDeliveryError as exc:
        # Another delivery already claimed this messageId and is (or was)
        # handling it — ack this one with 200 so Pub/Sub doesn't keep
        # redelivering a message that's already being taken care of.
        logger.info("[WORKER] %s — acking without reprocessing.", exc)
        return "", 200
    except InvalidJobPayload:
        logger.exception("[WORKER] Malformed job payload | messageId=%s delivery_attempt=%d", message_id, delivery_attempt)
        return "", 500
    except Exception:
        # job_processor.py already logged the specifics at the raise site
        # for every case that reaches here (DeliveryFailedError included,
        # since it subclasses Exception) — this is deliberately a bare
        # except Exception so ANY unexpected error still maps to a retry
        # instead of silently acking a failed job.
        return "", 500
    finally:
        clear_request()

    return "", 200


def _handle_poison(request: Request):
    """
    Handle a push delivery from the dead-letter subscription — best-effort
    notify the user their request permanently failed. Always acks (200)
    regardless of outcome: there is nowhere further for a poison-delivery
    failure to go (see terraform/main.tf's dead_letter_subscription — it
    deliberately has no dead_letter_policy of its own), so redelivering
    would only loop forever. Failures are logged, not retried.
    """
    envelope = request.get_json(silent=True)
    try:
        raw_payload, _message_id = unwrap_pubsub_envelope(envelope)
    except InvalidJobPayload:
        logger.error("[POISON] Malformed dead-letter push envelope — nothing to notify the user with.")
        return "", 200

    try:
        process_poison_job(raw_payload)
    except Exception:
        logger.exception("[POISON] Failed to notify user of a permanently failed job.")
    finally:
        clear_request()

    return "", 200


@functions_framework.http
def gti_bot_worker_http(request: Request):
    """HTTP entrypoint for the Worker Cloud Run function (2nd Gen)."""
    path, method = request.path, request.method

    if path == "/" and method == "GET":
        return jsonify({
            "status": "ok",
            "name": "Google Threat Intelligence Agentic Bot — Worker (GCP)",
            "version": "1.0.0",
            "platform": "Google Cloud Run function",
        })

    if path == "/health" and method == "GET":
        return jsonify({
            "status": "ok",
            "app": "gti-teams-bot-worker",
            "platform": "gcp",
            "gti_api_configured": bool(settings.gti_api_key),
        })

    if path == "/tasks/process" and method == "POST":
        return _handle_process(request)

    if path == "/tasks/poison" and method == "POST":
        return _handle_poison(request)

    return jsonify({"error": "Not found"}), 404
