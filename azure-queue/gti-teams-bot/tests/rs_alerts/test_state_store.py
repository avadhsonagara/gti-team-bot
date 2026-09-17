"""
Regression tests for the incremental cursor checkpoint (Azure Blob Storage).

Same mechanism as the canonical gti-ms-team-bot/gcp/rs-alerts implementation
exactly: a single `last_update_time` string, nothing else — read/write
round-trip, first-run (no blob yet), and a corrupted blob degrading to
"start fresh" rather than crashing the job.
"""
import json

import pytest
from azure.core.exceptions import ResourceNotFoundError

from app import state_store
from app.config import Settings


class _FakeBlobClient:
    def __init__(self, store: dict):
        self._store = store

    def download_blob(self):
        if "data" not in self._store:
            raise ResourceNotFoundError("no blob yet")
        data = self._store["data"]

        class _Downloaded:
            def readall(self_):
                return data

        return _Downloaded()

    def upload_blob(self, payload, overwrite=True):
        self._store["data"] = payload.encode() if isinstance(payload, str) else payload


@pytest.fixture
def fake_store(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(state_store, "_blob_client", lambda settings: _FakeBlobClient(store))
    return store


def _settings() -> Settings:
    return Settings(_env_file=None, gti_rsa_project="p")


def test_first_run_has_no_cursor(fake_store):
    assert state_store.read_cursor(_settings()) is None


def test_write_then_read_round_trip(fake_store):
    state_store.write_cursor(_settings(), "2026-09-17T10:00:00Z")
    assert state_store.read_cursor(_settings()) == "2026-09-17T10:00:00Z"


def test_write_overwrites_the_previous_cursor(fake_store):
    state_store.write_cursor(_settings(), "2026-09-17T10:00:00Z")
    state_store.write_cursor(_settings(), "2026-09-17T11:00:00Z")
    assert state_store.read_cursor(_settings()) == "2026-09-17T11:00:00Z"


def test_corrupt_blob_degrades_to_fresh_start_not_a_crash(fake_store):
    fake_store["data"] = b"{not valid json"
    assert state_store.read_cursor(_settings()) is None


def test_write_retries_transient_failures_then_succeeds(monkeypatch, fake_store):
    attempts = {"n": 0}
    real_client = state_store._blob_client(_settings())

    class _FlakyOnceClient:
        def upload_blob(self, payload, overwrite=True):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("transient blob write failure")
            real_client.upload_blob(payload, overwrite=overwrite)

    monkeypatch.setattr(state_store, "_blob_client", lambda settings: _FlakyOnceClient())
    monkeypatch.setattr(state_store.time, "sleep", lambda seconds: None)

    state_store.write_cursor(_settings(), "2026-09-17T10:00:00Z")
    assert attempts["n"] == 2
    # real_client shares fake_store's backing dict, so read the write's
    # actual effect back from there directly rather than through
    # _blob_client() again (still monkeypatched to the write-only
    # _FlakyOnceClient above, which has no download_blob).
    saved = json.loads(fake_store["data"])
    assert saved["last_update_time"] == "2026-09-17T10:00:00Z"


def test_write_raises_after_exhausting_all_retries(monkeypatch, fake_store):
    class _AlwaysFailsClient:
        def upload_blob(self, payload, overwrite=True):
            raise RuntimeError("persistent blob write failure")

    monkeypatch.setattr(state_store, "_blob_client", lambda settings: _AlwaysFailsClient())
    monkeypatch.setattr(state_store.time, "sleep", lambda seconds: None)

    with pytest.raises(RuntimeError, match="persistent blob write failure"):
        state_store.write_cursor(_settings(), "2026-09-17T10:00:00Z")
