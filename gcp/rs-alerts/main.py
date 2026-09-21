"""
Google Cloud Run function (2nd Gen) entrypoint for RS Alerts.

Exposes:
  - `rs_alerts_http`: HTTP-triggered entrypoint invoked on a schedule by Cloud Scheduler
    (via OIDC authentication) or called manually for testing/troubleshooting.
"""
import json
import logging
from flask import Request, jsonify
import functions_framework

from app.config import settings
from app.job import run_job
from app.logging_config import setup_logging

setup_logging()

logger = logging.getLogger("rs-alerts")


@functions_framework.http
def rs_alerts_http(request: Request):
    """
    HTTP entrypoint for Google Cloud Run function (2nd Gen).

    Invoked on schedule by Cloud Scheduler or manually for verification.
    """
    try:
        logger.info("RS Alerts function invoked via %s %s", request.method, request.path)
        summary = run_job(settings)
        logger.info("RS Alerts run summary: %s", summary)
        return jsonify(summary), 200
    except Exception as exc:
        logger.exception("RS Alerts job execution failed: %s", exc)
        return jsonify({"status": "error", "error": str(exc)}), 500


if __name__ == "__main__":
    import sys
    print("Running RS Alerts job directly from CLI...")
    try:
        res = run_job(settings)
        print("Success:", json.dumps(res, indent=2))
    except Exception as e:
        print("Error:", e, file=sys.stderr)
        sys.exit(1)
