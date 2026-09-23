"""
Inbound Bot Framework request authentication.

Verifies the JWT bearer token the Bot Framework Connector attaches to every
call to POST /api/messages: RS256 signature verified against Bot
Framework's public JWKS, issuer/audience checked, plus a serviceUrl-claim
match against the activity body's own serviceUrl to catch a token being
replayed against a spoofed destination. See
https://learn.microsoft.com/en-us/azure/bot-service/rest-api/bot-framework-rest-connector-authentication
for the underlying protocol.
"""
import logging

import jwt

logger = logging.getLogger("gti-teams-bot")

_JWKS_URI = "https://login.botframework.com/v1/.well-known/keys"
_TOKEN_ISSUER = "https://api.botframework.com"
_LEEWAY_SECONDS = 300  # matches Bot Framework's documented allowed clock skew

_jwks_client: "jwt.PyJWKClient | None" = None


class BotFrameworkAuthError(Exception):
    """Raised when an inbound request fails Bot Framework authentication."""


def _get_jwks_client() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = jwt.PyJWKClient(_JWKS_URI)
    return _jwks_client


def _expected_audiences(app_id: str) -> list[str]:
    return [app_id, f"api://{app_id}", f"api://botid-{app_id}"]


def _expected_issuers(tenant_id: str) -> list[str]:
    """
    Construct the list of valid issuer claims for an inbound activity token.

    The classic multi-tenant Bot Connector issuer (_TOKEN_ISSUER) is what
    real traffic actually carries here — confirmed live via an unverified
    decode of a rejected token on Azure's deployment of this same bot code.
    This function additionally accepts a tenant-scoped Entra issuer, matching
    a gap Microsoft's own current SDK acknowledges for some
    UserAssignedMSI/SingleTenant-hosted bots (microsoft/Agents-for-js#1152);
    harmless to allow defensively even though it isn't this bot's actual
    failure mode. Pinned to THIS bot's own tenant_id specifically, not a
    wildcard tenant pattern, to avoid accepting a token forged by a different
    tenant.
    """
    issuers = [_TOKEN_ISSUER]
    if tenant_id:
        issuers.append(f"https://sts.windows.net/{tenant_id}/")
        issuers.append(f"https://login.microsoftonline.com/{tenant_id}/v2.0")
    return issuers


def validate_bot_framework_token(
    authorization_header: str, app_id: str, claimed_service_url: str | None, tenant_id: str = "",
) -> dict:
    """
    Validate the Authorization header of an inbound /api/messages request.

    Returns the decoded token payload on success. Raises BotFrameworkAuthError
    on any failure: missing/malformed header, bad signature, wrong issuer or
    audience, expired token, a JWKS lookup failure, or a serviceUrl mismatch
    between the token and the activity body.
    """
    if not app_id:
        raise BotFrameworkAuthError("No CLIENT_ID configured — refusing all inbound requests.")

    if not authorization_header or not authorization_header.startswith("Bearer "):
        raise BotFrameworkAuthError("Missing or malformed Authorization header.")
    raw_token = authorization_header[len("Bearer "):]

    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(raw_token)
        payload = jwt.decode(
            raw_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=_expected_audiences(app_id),
            # Issuer is checked manually below, not via this option — PyJWT
            # versions vary in whether `issuer` accepts a list (2.8.0 doesn't,
            # silently treating it as never-matching even against its own
            # first element — reproduced and confirmed as the reason every
            # real Teams message was rejected in Azure's deployment, which
            # pins that version; GCP isn't currently affected since it pins
            # a newer PyJWT, but this avoids depending on that at all).
            options={
                "verify_signature": True,
                "verify_aud": True,
                "verify_iss": False,
                "verify_exp": True,
                "verify_iat": True,
            },
            leeway=_LEEWAY_SECONDS,
        )
    except jwt.PyJWTError as exc:
        # Covers both real validation failures (InvalidTokenError and its
        # subclasses) and JWKS lookup failures (PyJWKClientError) — neither
        # is a subclass of the other, and both mean "reject this request".
        raise BotFrameworkAuthError(f"Token validation failed: {exc}") from exc

    if payload.get("iss") not in _expected_issuers(tenant_id):
        raise BotFrameworkAuthError(f"Token validation failed: Invalid issuer {payload.get('iss')!r}")

    if not claimed_service_url:
        raise BotFrameworkAuthError("Missing serviceUrl in request body.")
    token_service_url = (payload.get("serviceurl") or "").rstrip("/")
    if token_service_url != claimed_service_url.rstrip("/"):
        raise BotFrameworkAuthError(
            f"serviceUrl mismatch: token claims {token_service_url!r}, activity body says {claimed_service_url!r}"
        )

    return payload
