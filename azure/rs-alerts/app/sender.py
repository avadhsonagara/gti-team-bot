"""
Posts GTI alert Adaptive Cards to a Microsoft Teams channel via an incoming
webhook (Teams Workflows / Power Automate's "Post to a channel when a
webhook request is received" template).
"""
import logging
import time
from collections.abc import Callable

import requests

from app.cards import build_alert_card
from app.config import Settings

logger = logging.getLogger("rs-alerts")


class AlertSender:
    """Post GTI alert Adaptive Cards to a Teams channel via an incoming webhook."""

    def __init__(
        self,
        settings: Settings,
        on_checkpoint: Callable[[str], None] | None = None,
    ):
        self._settings = settings
        self._on_checkpoint = on_checkpoint
        self.total_sent = 0

    def send(self, alert: dict) -> None:
        """Post one alert as an Adaptive Card to the configured Teams webhook."""
        payload = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": build_alert_card(alert, self._settings.gti_rsa_project),
                }
            ],
        }

        resp = requests.post(
            self._settings.rs_alerts_webhook_url,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()

        self.total_sent += 1
        audit = alert.get("audit", {})
        update_time = audit.get("updateTime") or audit.get("createTime")
        if self._on_checkpoint and update_time:
            self._on_checkpoint(update_time)
        elif self._on_checkpoint:
            logger.warning(
                "Alert %s has no audit.updateTime or audit.createTime — cursor "
                "cannot advance past it and it may be re-sent on the next run.",
                alert.get("name", "<unknown>"),
            )

        time.sleep(0.3)  # Rate-limit protection
