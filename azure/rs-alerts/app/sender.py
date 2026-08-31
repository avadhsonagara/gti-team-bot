"""
Posts GTI alert Adaptive Cards to a Microsoft Teams channel via the
Bot Framework Connector API (https://api.botframework.com).
"""
import logging
import re
import time
import urllib.parse
from collections.abc import Callable

import requests

from app.bot_auth import get_bot_token
from app.cards import build_alert_card
from app.config import Settings

logger = logging.getLogger("rs-alerts")


def extract_channel_id(raw_ref: str) -> str:
    """Parse a Teams channel link or bare channel ID into a canonical 19:...@thread.tacv2 ID."""
    decoded = urllib.parse.unquote(raw_ref.strip())
    match = re.search(r"(19:[a-zA-Z0-9_\-\.]+@(thread\.(tacv2|skype|v2)|skype))", decoded)
    if match:
        return match.group(1)
    if decoded.startswith("19:"):
        return decoded
    return raw_ref.strip()


class AlertSender:
    """Post GTI alert Adaptive Cards to a Teams channel via Bot Framework API."""

    def __init__(
        self,
        settings: Settings,
        channel_id: str,
        on_checkpoint: Callable[[str], None] | None = None,
    ):
        self._settings = settings
        self._channel_id = channel_id
        self._on_checkpoint = on_checkpoint
        self._bot_token: str | None = None
        self.total_sent = 0

    def _token(self) -> str:
        if not self._bot_token:
            self._bot_token = get_bot_token(self._settings)
        return self._bot_token

    def send(self, alert: dict) -> None:
        """Post one alert as an Adaptive Card to the target Teams channel."""
        service_url = self._settings.service_url
        url = f"{service_url}v3/conversations/{self._channel_id}/activities"

        payload = {
            "type": "message",
            "serviceUrl": service_url,
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": build_alert_card(alert, self._settings.gti_rsa_project),
                }
            ],
        }

        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()

        self.total_sent += 1
        update_time = alert.get("audit", {}).get("updateTime")
        if self._on_checkpoint and update_time:
            self._on_checkpoint(update_time)

        time.sleep(0.3)  # Rate-limit protection
