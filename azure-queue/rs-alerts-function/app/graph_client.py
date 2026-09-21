"""
Microsoft Graph client for automating Teams app installation.

Provides authentication and API interactions with Microsoft Graph to verify
and ensure that the Teams bot application is installed in the destination team.
"""
import logging

import requests
from azure.identity import ManagedIdentityCredential

from app.config import Settings

GRAPH_SCOPE = "https://graph.microsoft.com/.default"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

logger = logging.getLogger("rs-alerts")


def _get_graph_token_via_managed_identity(managed_identity_client_id: str) -> str:
    """
    Acquire a Microsoft Graph OAuth token using Azure Managed Identity.

    Args:
        managed_identity_client_id: Client ID of the User-Assigned Managed Identity.

    Returns:
        A valid OAuth bearer token for Microsoft Graph.

    Raises:
        azure.core.exceptions.ClientAuthenticationError: If token acquisition fails.
    """
    credential = ManagedIdentityCredential(client_id=managed_identity_client_id)
    return credential.get_token(GRAPH_SCOPE).token


def _get_graph_token(settings: Settings) -> str:
    """
    Retrieve a Microsoft Graph OAuth token using configured settings.

    Args:
        settings: Application settings containing managed identity configuration.

    Returns:
        OAuth bearer token string for Microsoft Graph.

    Raises:
        RuntimeError: If managed identity client ID is not configured.
    """
    if not settings.managed_identity_client_id:
        raise RuntimeError("MANAGED_IDENTITY_CLIENT_ID is not configured.")
    return _get_graph_token_via_managed_identity(settings.managed_identity_client_id)


def _get_catalog_app_id(token: str, external_id: str) -> str | None:
    """
    Look up the organization app catalog ID for a Teams app by external ID.

    Args:
        token: Microsoft Graph bearer token.
        external_id: External application ID (bot client ID) defined in manifest.

    Returns:
        Internal catalog app ID if found, otherwise None.

    Raises:
        requests.RequestException: If the catalog query request fails.
    """
    resp = requests.get(
        f"{GRAPH_BASE}/appCatalogs/teamsApps",
        headers={"Authorization": f"Bearer {token}"},
        params={"$filter": f"externalId eq '{external_id}'"},
        timeout=30,
    )
    resp.raise_for_status()
    values = resp.json().get("value", [])
    return values[0]["id"] if values else None


def ensure_app_installed(team_id: str, settings: Settings) -> None:
    """
    Ensure the bot application is installed in the target Microsoft Team.

    Checks the tenant app catalog and requests installation into the team.
    Failures are logged as warnings rather than raised to avoid aborting delivery
    if the bot is already installed or permissions are restricted.

    Args:
        team_id: Azure AD object ID of the destination Team.
        settings: Application settings containing credentials and app identifiers.
    """
    try:
        logger.info("[GRAPH-INSTALL] Checking app installation for team_id=%s", team_id)
        token = _get_graph_token(settings)

        catalog_app_id = _get_catalog_app_id(token, settings.client_id)
        if not catalog_app_id:
            logger.warning(
                "[GRAPH-INSTALL] Teams app (external id %s) not found in org app catalog. "
                "Upload it via Teams Admin Center or appCatalogs API. Skipping auto-install.",
                settings.client_id,
            )
            return

        resp = requests.post(
            f"{GRAPH_BASE}/teams/{team_id}/installedApps",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"teamsApp@odata.bind": f"{GRAPH_BASE}/appCatalogs/teamsApps/{catalog_app_id}"},
            timeout=30,
        )
        if resp.status_code in (200, 201):
            logger.info("[GRAPH-INSTALL] Successfully installed Teams app into team_id=%s.", team_id)
            return
        if resp.status_code == 409 or "already" in resp.text.lower():
            logger.info("[GRAPH-INSTALL] Teams app is already installed in team_id=%s.", team_id)
            return
        resp.raise_for_status()
    except Exception as exc:
        logger.warning(
            "[GRAPH-INSTALL] Could not auto-install Teams app into team_id=%s: %s. "
            "If the bot is not a member of this team, proactive delivery may fail.",
            team_id, exc,
        )
