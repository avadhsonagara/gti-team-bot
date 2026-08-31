"""
Bot Framework Connector API authentication.

Two credential paths, matching how the bot's Azure Bot resource may be
registered (see infra/main.bicep):

  - Managed identity (production): the Azure Bot is registered with
    msaAppType "UserAssignedMSI" and MANAGED_IDENTITY_CLIENT_ID names the
    same User-Assigned Managed Identity attached to this Function App.
    No client secret exists or is needed.
  - Client secret (local dev / classic app registration): falls back to
    the OAuth2 client-credentials grant against Microsoft Entra ID.
"""
import logging

import requests
from azure.identity import ManagedIdentityCredential

from app.config import Settings

BOTFRAMEWORK_SCOPE = "https://api.botframework.com/.default"

logger = logging.getLogger("rs-alerts")


def _get_bot_token_via_managed_identity(managed_identity_client_id: str) -> str:
    credential = ManagedIdentityCredential(client_id=managed_identity_client_id)
    token = credential.get_token(BOTFRAMEWORK_SCOPE)
    return token.token


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
    """Acquire a Bot Framework Connector API access token."""
    if settings.managed_identity_client_id:
        logger.info("Authenticating to Bot Framework via managed identity.")
        return _get_bot_token_via_managed_identity(settings.managed_identity_client_id)

    if settings.client_id and settings.client_secret and settings.tenant_id:
        logger.info("Authenticating to Bot Framework via client secret.")
        return _get_bot_token_via_client_secret(
            settings.client_id, settings.client_secret, settings.tenant_id
        )

    raise RuntimeError(
        "No Bot Framework credentials configured: set either MANAGED_IDENTITY_CLIENT_ID "
        "(Azure managed identity) or CLIENT_ID + CLIENT_SECRET + TENANT_ID (client secret)."
    )
