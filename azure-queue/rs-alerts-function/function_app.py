"""
Azure Functions entry point for the RS Alerts application.

Provides scheduled timer triggers and manual HTTP triggers to fetch incremental
Google Threat Intelligence (GTI) alerts and deliver them to Microsoft Teams channels.
"""
import json
import logging

import azure.functions as func

from app.config import settings
from app.job import run_job
from app.logging_config import setup_logging

setup_logging()

logger = logging.getLogger("rs-alerts")

app = func.FunctionApp()


@app.function_name(name="rs_alerts_timer")
@app.timer_trigger(
    schedule="%RS_ALERTS_SCHEDULE%",
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def rs_alerts_timer(timer: func.TimerRequest) -> None:
    """
    Execute the RS Alerts job on a periodic schedule defined by NCRONTAB configuration.

    Args:
        timer: TimerRequest metadata provided by the Azure Functions runtime.
    """
    if timer.past_due:
        logger.warning("[RS-ALERTS TIMER] Timer trigger invocation is running past due.")

    logger.info("[RS-ALERTS TIMER] Scheduled RS Alerts execution triggered.")
    try:
        summary = run_job(settings)
        logger.info(
            "[RS-ALERTS TIMER] Scheduled run completed successfully | fetched=%d cursor=%s -> %s",
            summary.get("fetched", 0),
            summary.get("cursor_from"),
            summary.get("cursor_to"),
        )
    except Exception:
        logger.exception("[RS-ALERTS TIMER] Scheduled RS Alerts execution failed.")
        raise


@app.function_name(name="rs_alerts_trigger")
@app.route(route="trigger", methods=["GET", "POST"], auth_level=func.AuthLevel.FUNCTION)
def rs_alerts_trigger(req: func.HttpRequest) -> func.HttpResponse:
    """
    Manually invoke the RS Alerts job on demand via an authenticated HTTP request.

    Args:
        req: HttpRequest object received by the function.

    Returns:
        HttpResponse containing the JSON run summary or error details.
    """
    logger.info("[RS-ALERTS HTTP] Manual trigger request received | method=%s url=%s", req.method, req.url)
    try:
        summary = run_job(settings)
        logger.info(
            "[RS-ALERTS HTTP] Manual trigger run completed successfully | fetched=%d cursor=%s -> %s",
            summary.get("fetched", 0),
            summary.get("cursor_from"),
            summary.get("cursor_to"),
        )
        return func.HttpResponse(
            json.dumps(summary), status_code=200, mimetype="application/json"
        )
    except Exception as exc:
        logger.exception("[RS-ALERTS HTTP] Manual trigger run failed.")
        return func.HttpResponse(
            json.dumps({"error": str(exc)}), status_code=500, mimetype="application/json"
        )

