"""
Microsoft Teams SDK App wiring.

Builds the microsoft_teams.apps.App bound to our FastAPI instance via
FastAPIAdapter.
"""
import logging
import os

from microsoft_teams.apps import App, FastAPIAdapter

from app.config import settings
from app.teams.handlers import handle_message

logger = logging.getLogger("gti-teams-bot")


def create_teams_app(fastapi_app) -> App:
    """
    Build and wire the Teams SDK App onto fastapi_app.
    """
    os.environ.setdefault("CLIENT_ID", settings.client_id)
    os.environ.setdefault("CLIENT_SECRET", settings.client_secret)
    os.environ.setdefault("TENANT_ID", settings.tenant_id)

    adapter = FastAPIAdapter(app=fastapi_app)
    teams_app = App(http_server_adapter=adapter)

    @teams_app.on_message
    async def _on_message(ctx) -> None:
        """Route inbound Teams message activity to handle_message."""
        await handle_message(ctx)

    logger.info(
        "[TEAMS] App wired to FastAPI adapter | client_id=%s tenant_id=%s",
        "set" if settings.client_id else "MISSING",
        "set" if settings.tenant_id else "MISSING",
    )
    return teams_app
