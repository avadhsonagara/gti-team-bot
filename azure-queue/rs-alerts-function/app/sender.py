"""
Posts GTI alert Adaptive Cards to a Microsoft Teams channel via the
Bot Framework Connector API (https://api.botframework.com). One Teams
message per alert.
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

_SEND_RETRIES = 3
_SEND_BACKOFF_SECONDS = 1.0
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _post_with_retry(url: str, headers: dict, payload: dict) -> requests.Response:
    """
    POST one Teams activity with retry-with-backoff — mirrors
    state_store.write_cursor's pattern. Without this, a single transient
    failure (429/5xx/network blip) aborts every remaining alert in the
    batch, deferred to the next scheduled run with nothing retried in
    between. Any other 4xx (401, 403, 404, ...) is a permanent/config
    problem — bad credentials, or the bot not being a member of the
    channel — that retrying won't fix, so those fail immediately instead
    of wasting three attempts on every alert in the batch.
    """
    last_exc: Exception | None = None
    for attempt in range(_SEND_RETRIES):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt == _SEND_RETRIES - 1:
                raise
            delay = _SEND_BACKOFF_SECONDS * (2 ** attempt)
            logger.warning(
                "Teams delivery request failed (%s) — retrying (attempt %d/%d) in %.1fs.",
                exc, attempt + 1, _SEND_RETRIES, delay,
            )
            time.sleep(delay)
            continue

        if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < _SEND_RETRIES - 1:
            delay = _SEND_BACKOFF_SECONDS * (2 ** attempt)
            logger.warning(
                "Teams delivery failed (status %d) — retrying (attempt %d/%d) in %.1fs.",
                resp.status_code, attempt + 1, _SEND_RETRIES, delay,
            )
            time.sleep(delay)
            continue

        resp.raise_for_status()
        return resp

    raise last_exc or RuntimeError("Teams delivery failed after retries.")


def extract_channel_id(raw_ref: str) -> str:
    """Parse a Teams channel link or bare channel ID into a canonical 19:...@thread.tacv2 ID."""
    decoded = urllib.parse.unquote(raw_ref.strip())
    match = re.search(r"(19:[a-zA-Z0-9_\-\.]+@(thread\.(tacv2|skype|v2)|skype))", decoded)
    if match:
        return match.group(1)
    if decoded.startswith("19:"):
        return decoded
    return raw_ref.strip()


def extract_team_id(raw_ref: str) -> str | None:
    """
    Parse the Team's underlying Microsoft 365 Group ID (the `groupId` query
    parameter) out of a full Teams channel link, if present.

    A Team's id — what Microsoft Graph's /teams/{team-id}/installedApps
    needs to auto-install the bot — is the same GUID as this group id. Only
    present when the *full* channel link is provided (not a bare
    19:...@thread.tacv2 ID), since a bare channel ID doesn't encode it.
    """
    decoded = urllib.parse.unquote(raw_ref.strip())
    query = urllib.parse.urlparse(decoded).query
    values = urllib.parse.parse_qs(query).get("groupId")
    return values[0] if values else None


class AlertSender:
    """Post GTI alert Adaptive Cards to a Teams channel via Bot Framework API — one message per alert."""

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

        _post_with_retry(
            url,
            headers={"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"},
            payload=payload,
        )

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
