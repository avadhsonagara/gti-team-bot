"""
Azure Functions (Python v2 programming model) entry point for RS Alerts.

Exposes two functions:
  - `rs_alerts_timer`: a Timer Trigger that runs the GTI -> Teams alert job
    on the schedule configured by the RS_ALERTS_SCHEDULE app setting
    (NCRONTAB expression; default: every 15 minutes). This is the
    production entry point — it's what "the background job" means.
  - `rs_alerts_trigger`: an HTTP Trigger (function-key protected) that runs
    the same job on demand, for manual testing and troubleshooting without
    waiting for the timer.
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
    """Run the RS Alerts job on the configured schedule."""
    if timer.past_due:
        logger.warning("RS Alerts timer trigger is running late.")

    try:
        summary = run_job(settings)
        logger.info("RS Alerts run summary: %s", summary)
    except Exception:
        logger.exception("RS Alerts job failed")
        raise


@app.function_name(name="rs_alerts_trigger")
@app.route(route="trigger", methods=["GET", "POST"], auth_level=func.AuthLevel.FUNCTION)
def rs_alerts_trigger(req: func.HttpRequest) -> func.HttpResponse:
    """Manually run the RS Alerts job on demand (for testing/troubleshooting)."""
    try:
        summary = run_job(settings)
        return func.HttpResponse(
            json.dumps(summary), status_code=200, mimetype="application/json"
        )
    except Exception as exc:
        logger.exception("RS Alerts job failed")
        return func.HttpResponse(
            json.dumps({"error": str(exc)}), status_code=500, mimetype="application/json"
        )
