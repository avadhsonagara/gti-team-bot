"""
Authentication helper for the Bot Framework Connector API.

Acquires access tokens using User-Assigned Managed Identity credentials
for outbound requests to Microsoft Teams.
"""
import logging

from azure.identity import ManagedIdentityCredential

from app.config import Settings

BOTFRAMEWORK_SCOPE = "https://api.botframework.com/.default"

logger = logging.getLogger("rs-alerts")


def _get_bot_token_via_managed_identity(managed_identity_client_id: str) -> str:
    """
    Obtain a Bot Framework OAuth token using Managed Identity.

    Args:
        managed_identity_client_id: Client ID of the User-Assigned Managed Identity.

    Returns:
        OAuth access token string.
    """
    credential = ManagedIdentityCredential(client_id=managed_identity_client_id)
    return credential.get_token(BOTFRAMEWORK_SCOPE).token


def get_bot_token(settings: Settings) -> str:
    """
    Acquire a Bot Framework Connector API access token using configured credentials.

    Args:
        settings: Application Settings instance containing managed identity configuration.

    Returns:
        Bearer token string for authenticating outbound Bot Framework requests.

    Raises:
        RuntimeError: If MANAGED_IDENTITY_CLIENT_ID is not configured.
    """
    if not settings.managed_identity_client_id:
        raise RuntimeError("MANAGED_IDENTITY_CLIENT_ID is not configured.")

    logger.info("[BOT-AUTH] Acquiring Bot Framework token via User-Assigned Managed Identity (%s)", settings.managed_identity_client_id)
    return _get_bot_token_via_managed_identity(settings.managed_identity_client_id)

