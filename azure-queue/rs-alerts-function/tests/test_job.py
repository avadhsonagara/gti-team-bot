"""
Regression tests for run_job()'s orchestration: required-config validation,
backfill-days fallback, and that the checkpoint fires once per successfully
sent alert — the same mechanism as the canonical gti-ms-team-bot/gcp/rs-alerts
implementation (see test_gti_client_filter.py for the matching strict `>`
filter behavior this checkpoint pairs with).
"""
from datetime import datetime, timezone

from app import job as job_module
from app.config import Settings


class _FakeSender:
    """Stands in for AlertSender — records every alert "sent" and mirrors its real checkpoint call."""

    def __init__(self, settings, channel_id, on_checkpoint=None):
        self._on_checkpoint = on_checkpoint
        self.sent: list[str] = []

    def send(self, alert):
        self.sent.append(alert["name"])
        audit = alert.get("audit", {})
        update_time = audit.get("updateTime") or audit.get("createTime")
        if self._on_checkpoint and update_time:
            self._on_checkpoint(update_time)


def _alert(short_id: str, update_time: str) -> dict:
    return {"name": f"projects/p/alerts/{short_id}", "audit": {"updateTime": update_time}}


def test_checkpoint_advances_once_per_sent_alert(monkeypatch):
    settings = Settings(
        _env_file=None,
        teams_channel_link_or_id="19:abc@thread.tacv2",
        gti_api_key="key",
        gti_rsa_project="p",
        managed_identity_client_id="mi-client-id",
    )

    monkeypatch.setattr(job_module, "read_cursor", lambda s: "2026-09-17T10:00:00Z")
    writes: list[str] = []
    monkeypatch.setattr(job_module, "write_cursor", lambda s, t: writes.append(t))
    monkeypatch.setattr(job_module, "ensure_app_installed", lambda team_id, s: None)
    monkeypatch.setattr(job_module, "get_gti_access_token", lambda key: "fake-token")
    monkeypatch.setattr(job_module, "AlertSender", _FakeSender)

    fetched_alerts = [
        _alert("alert-A", "2026-09-17T10:30:00Z"),
        _alert("alert-B", "2026-09-17T11:00:00Z"),
    ]
    monkeypatch.setattr(
        job_module, "list_alerts",
        lambda token, project, filter_str, page_size: iter(fetched_alerts),
    )

    summary = job_module.run_job(settings)

    assert summary["fetched"] == 2
    assert summary["cursor_from"] == "2026-09-17T10:00:00Z"
    assert summary["cursor_to"] == "2026-09-17T11:00:00Z"
    # One checkpoint write per successfully sent alert, in order.
    assert writes == ["2026-09-17T10:30:00Z", "2026-09-17T11:00:00Z"]


def test_missing_required_config_raises_before_any_gti_call(monkeypatch):
    settings = Settings(_env_file=None)  # nothing set

    called = {"list_alerts": False}
    monkeypatch.setattr(
        job_module, "list_alerts",
        lambda *a, **kw: called.__setitem__("list_alerts", True) or iter([]),
    )

    try:
        job_module.run_job(settings)
        raise AssertionError("expected RuntimeError for missing required config")
    except RuntimeError as exc:
        assert "TEAMS_CHANNEL_LINK_OR_ID" in str(exc)
        assert "GTI_API_KEY" in str(exc)
        assert "GTI_RSA_PROJECT" in str(exc)

    assert called["list_alerts"] is False, "must fail fast before ever calling GTI"


def test_missing_managed_identity_raises(monkeypatch):
    settings = Settings(
        _env_file=None,
        teams_channel_link_or_id="19:abc@thread.tacv2",
        gti_api_key="key",
        gti_rsa_project="p",
        # managed_identity_client_id deliberately left unset
    )
    try:
        job_module.run_job(settings)
        raise AssertionError("expected RuntimeError for missing MANAGED_IDENTITY_CLIENT_ID")
    except RuntimeError as exc:
        assert "MANAGED_IDENTITY_CLIENT_ID" in str(exc)


def test_backfill_days_out_of_range_falls_back_to_default(monkeypatch):
    settings = Settings(
        _env_file=None,
        teams_channel_link_or_id="19:abc@thread.tacv2",
        gti_api_key="key",
        gti_rsa_project="p",
        managed_identity_client_id="mi-client-id",
        backfill_days=30,  # out of the allowed 1-7 range
    )
    monkeypatch.setattr(job_module, "read_cursor", lambda s: None)
    monkeypatch.setattr(job_module, "write_cursor", lambda s, t: None)
    monkeypatch.setattr(job_module, "ensure_app_installed", lambda team_id, s: None)
    monkeypatch.setattr(job_module, "get_gti_access_token", lambda key: "fake-token")
    monkeypatch.setattr(job_module, "AlertSender", _FakeSender)
    monkeypatch.setattr(job_module, "list_alerts", lambda *a, **kw: iter([]))

    summary = job_module.run_job(settings)

    # Falls back to the documented default (7 days) rather than honoring the out-of-range value.
    cursor_dt = datetime.strptime(summary["cursor_from"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - cursor_dt).total_seconds() / 86400
    assert 6.9 < age_days < 7.1
