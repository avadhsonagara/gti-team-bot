"""
Tests for inbound Bot Framework JWT authentication.

Verifies token signature, issuer, audience, expiration, and serviceUrl claim matching
against inbound activity request data.
"""
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.teams import auth

_APP_ID = "11111111-1111-1111-1111-111111111111"
_ISSUER = "https://api.botframework.com"


@pytest.fixture(scope="module")
def rsa_key_pair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def _sign_token(private_key, service_url: str | None = "https://smba.trafficmanager.net/amer/", audience: str = _APP_ID, issuer: str = _ISSUER, expired: bool = False) -> str:
    now = int(time.time())
    payload = {
        "aud": audience,
        "iss": issuer,
        "iat": now - 1000,
        "exp": now - 1000 if expired else now + 300,
    }
    if service_url is not None:
        payload["serviceurl"] = service_url
    return jwt.encode(payload, private_key, algorithm="RS256")


@pytest.fixture
def patched_jwks(monkeypatch, rsa_key_pair):
    """Bypass the real network JWKS lookup — return our own test key's public half instead."""
    _, public_key = rsa_key_pair

    class _FakeSigningKey:
        key = public_key

    class _FakeJWKSClient:
        def get_signing_key_from_jwt(self, token):
            return _FakeSigningKey()

    monkeypatch.setattr(auth, "_get_jwks_client", lambda: _FakeJWKSClient())


def test_missing_authorization_header_rejected(patched_jwks):
    with pytest.raises(auth.BotFrameworkAuthError):
        auth.validate_bot_framework_token("", _APP_ID, "https://smba.trafficmanager.net/amer/")


def test_malformed_authorization_header_rejected(patched_jwks):
    with pytest.raises(auth.BotFrameworkAuthError):
        auth.validate_bot_framework_token("Basic abc123", _APP_ID, "https://smba.trafficmanager.net/amer/")


def test_valid_token_and_matching_service_url_accepted(patched_jwks, rsa_key_pair):
    private_key, _ = rsa_key_pair
    token = _sign_token(private_key, service_url="https://smba.trafficmanager.net/amer/")
    payload = auth.validate_bot_framework_token(f"Bearer {token}", _APP_ID, "https://smba.trafficmanager.net/amer/")
    assert payload["serviceurl"] == "https://smba.trafficmanager.net/amer/"


def test_service_url_mismatch_rejected(patched_jwks, rsa_key_pair):
    private_key, _ = rsa_key_pair
    token = _sign_token(private_key, service_url="https://smba.trafficmanager.net/amer/")
    with pytest.raises(auth.BotFrameworkAuthError, match="serviceUrl mismatch"):
        auth.validate_bot_framework_token(f"Bearer {token}", _APP_ID, "https://attacker.example.com/")


def test_missing_service_url_in_activity_body_rejected(patched_jwks, rsa_key_pair):
    private_key, _ = rsa_key_pair
    token = _sign_token(private_key, service_url="https://smba.trafficmanager.net/amer/")
    with pytest.raises(auth.BotFrameworkAuthError, match="Missing serviceUrl"):
        auth.validate_bot_framework_token(f"Bearer {token}", _APP_ID, None)
    with pytest.raises(auth.BotFrameworkAuthError, match="Missing serviceUrl"):
        auth.validate_bot_framework_token(f"Bearer {token}", _APP_ID, "")


def test_expired_token_rejected(patched_jwks, rsa_key_pair):
    private_key, _ = rsa_key_pair
    token = _sign_token(private_key, expired=True)
    with pytest.raises(auth.BotFrameworkAuthError):
        auth.validate_bot_framework_token(f"Bearer {token}", _APP_ID, "https://smba.trafficmanager.net/amer/")


def test_wrong_audience_rejected(patched_jwks, rsa_key_pair):
    private_key, _ = rsa_key_pair
    token = _sign_token(private_key, audience="22222222-2222-2222-2222-222222222222")
    with pytest.raises(auth.BotFrameworkAuthError):
        auth.validate_bot_framework_token(f"Bearer {token}", _APP_ID, "https://smba.trafficmanager.net/amer/")


def test_no_app_id_configured_rejects_everything(patched_jwks):
    with pytest.raises(auth.BotFrameworkAuthError):
        auth.validate_bot_framework_token("Bearer anything", "", "https://smba.trafficmanager.net/amer/")


_TENANT_ID = "3adb963c-8e61-48e8-a06d-6dbb0dacea39"
_OTHER_TENANT_ID = "99999999-9999-9999-9999-999999999999"


def test_tenant_scoped_v1_issuer_accepted_for_msi_bots(patched_jwks, rsa_key_pair):
    """Defense-in-depth: some UserAssignedMSI/SingleTenant bots' tokens carry this issuer per Microsoft's own SDK gap, even though Azure's real tokens for this same bot code carry the classic one (confirmed live)."""
    private_key, _ = rsa_key_pair
    token = _sign_token(private_key, issuer=f"https://sts.windows.net/{_TENANT_ID}/")
    payload = auth.validate_bot_framework_token(
        f"Bearer {token}", _APP_ID, "https://smba.trafficmanager.net/amer/", _TENANT_ID,
    )
    assert payload["serviceurl"] == "https://smba.trafficmanager.net/amer/"


def test_tenant_scoped_v2_issuer_accepted_for_msi_bots(patched_jwks, rsa_key_pair):
    private_key, _ = rsa_key_pair
    token = _sign_token(private_key, issuer=f"https://login.microsoftonline.com/{_TENANT_ID}/v2.0")
    payload = auth.validate_bot_framework_token(
        f"Bearer {token}", _APP_ID, "https://smba.trafficmanager.net/amer/", _TENANT_ID,
    )
    assert payload["serviceurl"] == "https://smba.trafficmanager.net/amer/"


def test_tenant_scoped_issuer_from_a_different_tenant_still_rejected(patched_jwks, rsa_key_pair):
    """The tenant-scoped issuer allowance must be pinned to OUR tenant_id, not any tenant's."""
    private_key, _ = rsa_key_pair
    token = _sign_token(private_key, issuer=f"https://sts.windows.net/{_OTHER_TENANT_ID}/")
    with pytest.raises(auth.BotFrameworkAuthError):
        auth.validate_bot_framework_token(
            f"Bearer {token}", _APP_ID, "https://smba.trafficmanager.net/amer/", _TENANT_ID,
        )


def test_tenant_scoped_issuer_rejected_when_tenant_id_not_configured(patched_jwks, rsa_key_pair):
    """Without a configured tenant_id, only the classic issuer is accepted — no silent widening."""
    private_key, _ = rsa_key_pair
    token = _sign_token(private_key, issuer=f"https://sts.windows.net/{_TENANT_ID}/")
    with pytest.raises(auth.BotFrameworkAuthError):
        auth.validate_bot_framework_token(f"Bearer {token}", _APP_ID, "https://smba.trafficmanager.net/amer/")
