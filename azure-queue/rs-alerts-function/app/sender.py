"""
Teams alert message sender module.

Posts GTI alert Adaptive Cards to a Microsoft Teams channel via the
Bot Framework Connector API (v3/conversations/{channel_id}/activities).
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
    POST an activity payload to the Bot Framework endpoint with retry on transient errors.

    Retries with exponential backoff on network failures and HTTP status codes 429,
    500, 502, 503, and 504. Client configuration errors (e.g. 401, 403, 404) are
    raised immediately without retrying.

    Args:
        url: Teams channel activities URL endpoint.
        headers: HTTP headers including bearer authorization and content type.
        payload: Activity payload containing the Adaptive Card attachment.

    Returns:
        The successful requests.Response object.

    Raises:
        requests.RequestException: If the activity post fails after all retries.
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
                "[RS-ALERTS RETRY] Network error delivering activity (%s) — retrying attempt %d/%d in %.1fs.",
                exc, attempt + 1, _SEND_RETRIES, delay,
            )
            time.sleep(delay)
            continue

        if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < _SEND_RETRIES - 1:
            delay = _SEND_BACKOFF_SECONDS * (2 ** attempt)
            logger.warning(
                "[RS-ALERTS RETRY] Teams delivery returned HTTP %d — retrying attempt %d/%d in %.1fs.",
                resp.status_code, attempt + 1, _SEND_RETRIES, delay,
            )
            time.sleep(delay)
            continue

        resp.raise_for_status()
        return resp

    raise last_exc or RuntimeError("Teams delivery failed after retries.")


def extract_channel_id(raw_ref: str) -> str:
    """
    Extract a canonical Teams channel ID from a channel link or bare identifier.

    Args:
        raw_ref: Either a full Teams channel link or a raw channel ID string.

    Returns:
        Canonical channel identifier in format '19:...@thread.tacv2'.
    """
    decoded = urllib.parse.unquote(raw_ref.strip())
    match = re.search(r"(19:[a-zA-Z0-9_\-\.]+@(thread\.(tacv2|skype|v2)|skype))", decoded)
    if match:
        return match.group(1)
    if decoded.startswith("19:"):
        return decoded
    return raw_ref.strip()


def extract_team_id(raw_ref: str) -> str | None:
    """
    Extract the underlying Microsoft 365 Group ID (team ID) from a Teams channel link.

    Args:
        raw_ref: Full Teams channel link containing query parameters.

    Returns:
        The team/group GUID string if found, otherwise None.
    """
    decoded = urllib.parse.unquote(raw_ref.strip())
    query = urllib.parse.urlparse(decoded).query
    values = urllib.parse.parse_qs(query).get("groupId")
    return values[0] if values else None


class AlertSender:
    """
    Delivers GTI alert Adaptive Cards to a target Microsoft Teams channel.

    Handles token acquisition, card formatting, activity posting, rate limit spacing,
    and triggering checkpoint updates upon each successfully delivered alert.
    """

    def __init__(
        self,
        settings: Settings,
        channel_id: str,
        on_checkpoint: Callable[[str], None] | None = None,
    ):
        """
        Initialize the AlertSender instance.

        Args:
            settings: Application settings containing service URL and credentials.
            channel_id: Destination Teams channel ID.
            on_checkpoint: Optional callback invoked with the alert timestamp after delivery.
        """
        self._settings = settings
        self._channel_id = channel_id
        self._on_checkpoint = on_checkpoint
        self._bot_token: str | None = None
        self.total_sent = 0

    def _token(self) -> str:
        """
        Retrieve or cache the Bot Framework bearer access token.

        Returns:
            Bearer token string.
        """
        if not self._bot_token:
            self._bot_token = get_bot_token(self._settings)
        return self._bot_token

    def send(self, alert: dict) -> None:
        """
        Post an alert as an Adaptive Card to the configured Teams channel.

        Args:
            alert: GTI alert dictionary representation.

        Raises:
            requests.RequestException: If delivery fails after retries.
        """
        service_url = self._settings.service_url
        url = f"{service_url}v3/conversations/{self._channel_id}/activities"

        alert_name = alert.get("name", "<unknown>")
        logger.info(
            "[RS-ALERTS DELIVER] Posting alert %s (%d sent so far) to channel=%s",
            alert_name, self.total_sent + 1, self._channel_id,
        )

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
                "[RS-ALERTS DELIVER] Alert %s has no audit.updateTime or audit.createTime — "
                "cursor cannot advance past it and it may be re-sent on subsequent runs.",
                alert_name,
            )

        time.sleep(0.3)  # Rate-limit protection
