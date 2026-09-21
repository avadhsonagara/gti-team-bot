"""
Bot Framework Connector API authentication for Google Cloud Platform.

Supports authentication via Microsoft Entra ID OAuth2 client-credentials grant
(CLIENT_ID, CLIENT_SECRET, TENANT_ID).
"""
import logging
import requests

from app.config import Settings

BOTFRAMEWORK_SCOPE = "https://api.botframework.com/.default"

logger = logging.getLogger("rs-alerts")


def _get_bot_token_via_client_secret(client_id: str, client_secret: str, tenant_id: str) -> str:
    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": BOTFRAMEWORK_SCOPE,
        "grant_type": "client_credentials",
    }
    resp = requests.post(url, data=data, timeout=30)
    resp.raise_for_status()
    return resp.json()["access_token"]


def get_bot_token(settings: Settings) -> str:
    """Acquire a Bot Framework Connector API access token using Client Secret."""
    if settings.client_id and settings.client_secret and settings.tenant_id:
        logger.info("Authenticating to Bot Framework via Entra ID client secret.")
        return _get_bot_token_via_client_secret(
            settings.client_id, settings.client_secret, settings.tenant_id
        )

    raise RuntimeError(
        "Missing Bot Framework credentials: set CLIENT_ID + CLIENT_SECRET + TENANT_ID."
    )
