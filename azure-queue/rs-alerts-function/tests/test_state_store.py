"""Unit tests for incremental cursor persistence in Azure Blob Storage."""
import json

import pytest
from azure.core.exceptions import ResourceNotFoundError

from app import state_store
from app.config import Settings


class _FakeBlobClient:
    """In-memory mock for Azure BlobClient read/write operations."""

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
    """Fixture providing an in-memory dictionary backing the fake blob storage."""
    store: dict = {}
    monkeypatch.setattr(state_store, "_blob_client", lambda settings: _FakeBlobClient(store))
    return store


def _settings() -> Settings:
    """Helper creating a minimal Settings test instance."""
    return Settings(_env_file=None, gti_rsa_project="p")


def test_first_run_has_no_cursor(fake_store):
    """Verify read_cursor returns None when state blob does not exist."""
    assert state_store.read_cursor(_settings()) is None


def test_write_then_read_round_trip(fake_store):
    """Verify writing a cursor timestamp persists and can be read back accurately."""
    state_store.write_cursor(_settings(), "2026-09-17T10:00:00Z")
    assert state_store.read_cursor(_settings()) == "2026-09-17T10:00:00Z"


def test_write_overwrites_the_previous_cursor(fake_store):
    """Verify successive cursor writes overwrite earlier timestamps."""
    state_store.write_cursor(_settings(), "2026-09-17T10:00:00Z")
    state_store.write_cursor(_settings(), "2026-09-17T11:00:00Z")
    assert state_store.read_cursor(_settings()) == "2026-09-17T11:00:00Z"


def test_corrupt_blob_degrades_to_fresh_start_not_a_crash(fake_store):
    """Verify corrupted JSON blob payload degrades to None without raising exceptions."""
    fake_store["data"] = b"{not valid json"
    assert state_store.read_cursor(_settings()) is None


def test_write_retries_transient_failures_then_succeeds(monkeypatch, fake_store):
    """Verify write_cursor retries on transient exception and succeeds on next attempt."""
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
    saved = json.loads(fake_store["data"])
    assert saved["last_update_time"] == "2026-09-17T10:00:00Z"


def test_write_raises_after_exhausting_all_retries(monkeypatch, fake_store):
    """Verify write_cursor propagates exception after exhausting all retry attempts."""
    class _AlwaysFailsClient:
        def upload_blob(self, payload, overwrite=True):
            raise RuntimeError("persistent blob write failure")

    monkeypatch.setattr(state_store, "_blob_client", lambda settings: _AlwaysFailsClient())
    monkeypatch.setattr(state_store.time, "sleep", lambda seconds: None)

    with pytest.raises(RuntimeError, match="persistent blob write failure"):
        state_store.write_cursor(_settings(), "2026-09-17T10:00:00Z")

