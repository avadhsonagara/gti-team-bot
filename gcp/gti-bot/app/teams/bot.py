"""
Microsoft Teams SDK App wiring.

Builds the microsoft_teams.apps.App bound to our FastAPI instance via
FastAPIAdapter.
"""
import asyncio
import logging
import os

import jwt
from microsoft_teams.apps import App, FastAPIAdapter
from microsoft_teams.apps.auth.token_validator import JWT_LEEWAY_SECONDS, TokenValidator

from app.config import settings
from app.teams.handlers import handle_message

logger = logging.getLogger("gti-teams-bot")


def _patch_token_validator_for_async_jwks() -> None:
    """
    TokenValidator.validate_token() (microsoft-teams-apps SDK) fetches the
    JWKS signing key via a synchronous, blocking call
    (`self._jwks_client.get_signing_key_from_jwt`) with no `await`.

    main.py's Cloud Functions entrypoint bridges each request onto one
    persistent background event loop per worker (see _get_loop() there) —
    a blocking call anywhere freezes that loop for ALL in-flight and future
    requests (including unrelated routes) until it resolves or Cloud Run's
    own request timeout kills it. This hits on every inbound Teams message,
    since TokenValidator.for_service() validates every one.

    Patch validate_token to offload just the blocking fetch to a thread
    via asyncio.to_thread, so a slow/stuck JWKS lookup can no longer wedge
    the whole container. Mirrors the original method's logic exactly.
    """
    if getattr(TokenValidator, "_gti_jwks_offloaded", False):
        return

    async def validate_token(self, raw_token, service_url=None, scope=None):
        if not raw_token:
            logger.error("No token provided")
            raise jwt.InvalidTokenError("No token provided")

        try:
            signing_key = await asyncio.to_thread(
                self._jwks_client.get_signing_key_from_jwt, raw_token
            )

            payload = jwt.decode(
                raw_token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.options.valid_audiences,
                issuer=self.options.valid_issuers,
                options={
                    "verify_signature": True,
                    "verify_aud": True,
                    "verify_iss": bool(self.options.valid_issuers),
                    "verify_exp": True,
                    "verify_iat": True,
                },
                leeway=JWT_LEEWAY_SECONDS,
            )

            effective_service_url = service_url or self.options.service_url
            if effective_service_url:
                self._validate_service_url(payload, effective_service_url)

            required_scope = scope or self.options.scope
            if required_scope:
                self._validate_scope(payload, required_scope)

            logger.debug("Token validation successful")
            return payload

        except jwt.InvalidTokenError as e:
            logger.error(f"Token validation failed: {e}")
            raise

    TokenValidator.validate_token = validate_token
    TokenValidator._gti_jwks_offloaded = True
    logger.info("[TEAMS] Patched TokenValidator.validate_token to offload JWKS fetch to a thread.")


_patch_token_validator_for_async_jwks()


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
