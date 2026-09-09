"""
Bot Framework Connector API authentication.

Managed-Identity-only: this is a production job, and the User-Assigned
Managed Identity (MANAGED_IDENTITY_CLIENT_ID) is always available since
it's provisioned together with the Function App by azure/infra/main.bicep —
no client secret ever enters this codebase.
"""
import logging

from azure.identity import ManagedIdentityCredential

from app.config import Settings

BOTFRAMEWORK_SCOPE = "https://api.botframework.com/.default"

logger = logging.getLogger("rs-alerts")


def _get_bot_token_via_managed_identity(managed_identity_client_id: str) -> str:
    credential = ManagedIdentityCredential(client_id=managed_identity_client_id)
    return credential.get_token(BOTFRAMEWORK_SCOPE).token


def get_bot_token(settings: Settings) -> str:
    """Acquire a Bot Framework Connector API access token."""
    if not settings.managed_identity_client_id:
        raise RuntimeError("MANAGED_IDENTITY_CLIENT_ID is not configured.")

    logger.info("Authenticating to Bot Framework via User-Assigned Managed Identity.")
    return _get_bot_token_via_managed_identity(settings.managed_identity_client_id)
