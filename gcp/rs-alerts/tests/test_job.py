"""Unit tests for the RS Alerts run_job orchestration workflow."""
from datetime import datetime, timezone

import pytest

from app import job as job_module
from app.config import Settings


class _FakeSender:
    """Mock AlertSender recording sent alerts and triggering checkpoint callbacks."""

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
        teams_channel_id="19:abc@thread.tacv2",
        gti_api_key="key",
        gti_rsa_project="p",
        client_id="c", client_secret="s", tenant_id="t",
    )

    monkeypatch.setattr(job_module, "read_cursor", lambda s: "2026-09-17T10:00:00Z")
    writes: list[str] = []
    monkeypatch.setattr(job_module, "write_cursor", lambda s, t: writes.append(t))
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
    assert writes == ["2026-09-17T10:30:00Z", "2026-09-17T11:00:00Z"]


def test_missing_required_config_raises_before_any_gti_call(monkeypatch):
    settings = Settings(_env_file=None)

    called = {"list_alerts": False}
    monkeypatch.setattr(
        job_module, "list_alerts",
        lambda *a, **kw: called.__setitem__("list_alerts", True) or iter([]),
    )

    try:
        job_module.run_job(settings)
        raise AssertionError("expected RuntimeError for missing required config")
    except RuntimeError as exc:
        assert "TEAMS_CHANNEL_ID" in str(exc)
        assert "GTI_API_KEY" in str(exc)
        assert "GTI_RSA_PROJECT" in str(exc)

    assert called["list_alerts"] is False


def test_missing_bot_framework_credentials_raises(monkeypatch):
    settings = Settings(
        _env_file=None,
        teams_channel_id="19:abc@thread.tacv2",
        gti_api_key="key",
        gti_rsa_project="p",
    )
    try:
        job_module.run_job(settings)
        raise AssertionError("expected RuntimeError for missing Bot Framework credentials")
    except RuntimeError as exc:
        assert "CLIENT_ID" in str(exc)
        assert "CLIENT_SECRET" in str(exc)
        assert "TENANT_ID" in str(exc)


def test_backfill_days_out_of_range_falls_back_to_default(monkeypatch):
    settings = Settings(
        _env_file=None,
        teams_channel_id="19:abc@thread.tacv2",
        gti_api_key="key",
        gti_rsa_project="p",
        client_id="c", client_secret="s", tenant_id="t",
        backfill_days=30,
    )
    monkeypatch.setattr(job_module, "read_cursor", lambda s: None)
    monkeypatch.setattr(job_module, "write_cursor", lambda s, t: None)
    monkeypatch.setattr(job_module, "get_gti_access_token", lambda key: "fake-token")
    monkeypatch.setattr(job_module, "AlertSender", _FakeSender)
    monkeypatch.setattr(job_module, "list_alerts", lambda *a, **kw: iter([]))

    summary = job_module.run_job(settings)

    cursor_dt = datetime.strptime(summary["cursor_from"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - cursor_dt).total_seconds() / 86400
    assert 6.9 < age_days < 7.1


def test_job_failure_is_logged_and_reraised(monkeypatch, caplog):
    """[RS-ALERTS FAILED] must fire and the original exception must still propagate."""
    settings = Settings(
        _env_file=None,
        teams_channel_id="19:abc@thread.tacv2",
        gti_api_key="key",
        gti_rsa_project="p",
        client_id="c", client_secret="s", tenant_id="t",
    )
    monkeypatch.setattr(job_module, "read_cursor", lambda s: "2026-09-17T10:00:00Z")

    def _raise(key):
        raise RuntimeError("GTI is down")
    monkeypatch.setattr(job_module, "get_gti_access_token", _raise)

    with pytest.raises(RuntimeError, match="GTI is down"):
        with caplog.at_level("ERROR", logger="rs-alerts"):
            job_module.run_job(settings)

    assert any("[RS-ALERTS FAILED]" in rec.message for rec in caplog.records)
