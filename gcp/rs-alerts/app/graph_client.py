"""
Microsoft Graph client for automating Teams app installation.

Reuses the same Entra ID client-secret credentials as bot_auth.py — the only
difference is the requested token's scope (Microsoft Graph instead of the
Bot Framework Connector API). Once that app registration has been granted
the TeamsAppInstallation.ReadWriteForTeam.All application permission with
admin consent, it can install the bot's Teams app into a team via API, so
nobody has to manually click "Add" in the Teams UI for every new team.
"""
import logging

import requests

from app.config import Settings

GRAPH_SCOPE = "https://graph.microsoft.com/.default"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

logger = logging.getLogger("rs-alerts")


def _get_graph_token(settings: Settings) -> str:
    if not (settings.client_id and settings.client_secret and settings.tenant_id):
        raise RuntimeError(
            "No Azure AD credentials configured for Microsoft Graph: set "
            "CLIENT_ID + CLIENT_SECRET + TENANT_ID."
        )
    url = f"https://login.microsoftonline.com/{settings.tenant_id}/oauth2/v2.0/token"
    data = {
        "client_id": settings.client_id,
        "client_secret": settings.client_secret,
        "scope": GRAPH_SCOPE,
        "grant_type": "client_credentials",
    }
    resp = requests.post(url, data=data, timeout=30)
    resp.raise_for_status()
    return resp.json()["access_token"]


def _get_catalog_app_id(token: str, external_id: str) -> str | None:
    """Look up the org app catalog's internal id for a Teams app by its external (bot) id."""
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
    Install the bot's Teams app into the given team if it isn't already
    there. Requires the app to already be uploaded to the org's app catalog
    (Teams Admin Center, or POST /appCatalogs/teamsApps) and the configured
    app registration to have been granted
    TeamsAppInstallation.ReadWriteForTeam.All with admin consent — both
    one-time setup steps. Failures here are logged, not raised, so a
    transient Graph issue doesn't block delivery to a team the bot may
    already be a member of.
    """
    try:
        token = _get_graph_token(settings)

        catalog_app_id = _get_catalog_app_id(token, settings.client_id)
        if not catalog_app_id:
            logger.warning(
                "Teams app (external id %s) not found in the org app catalog — "
                "upload it once via Teams Admin Center or POST /appCatalogs/teamsApps "
                "before auto-install can work. Skipping.",
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
            logger.info("Installed Teams app into team %s.", team_id)
            return
        if resp.status_code == 409 or "already" in resp.text.lower():
            logger.info("Teams app already installed in team %s.", team_id)
            return
        resp.raise_for_status()
    except Exception as exc:
        logger.warning(
            "Could not auto-install Teams app into team %s (%s) — if the bot "
            "isn't already a member of this team, message delivery will fail.",
            team_id, exc,
        )
