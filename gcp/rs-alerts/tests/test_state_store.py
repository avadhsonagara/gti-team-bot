"""Unit tests for incremental cursor persistence in Google Cloud Firestore."""
import pytest

from app import state_store
from app.config import Settings


class _FakeDoc:
    """In-memory mock for a Firestore DocumentSnapshot."""

    def __init__(self, data: dict | None):
        self._data = data

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict | None:
        return self._data


class _FakeDocRef:
    """In-memory mock for a Firestore DocumentReference read/write operations."""

    def __init__(self, store: dict):
        self._store = store

    def get(self) -> _FakeDoc:
        if "data" not in self._store:
            return _FakeDoc(None)
        return _FakeDoc(self._store["data"])

    def set(self, data: dict, merge: bool = True) -> None:
        self._store["data"] = dict(data)


class _FakeCollection:
    def __init__(self, store: dict):
        self._store = store

    def document(self, doc_id: str) -> _FakeDocRef:
        return _FakeDocRef(self._store)


class _FakeFirestoreClient:
    def __init__(self, store: dict):
        self._store = store

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self._store)


@pytest.fixture
def fake_store(monkeypatch):
    """Fixture providing an in-memory dictionary backing the fake Firestore client."""
    store: dict = {}
    monkeypatch.setattr(state_store, "_get_firestore_client", lambda settings: _FakeFirestoreClient(store))
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


def test_document_with_no_timestamp_field_degrades_to_fresh_start(fake_store):
    """A Firestore document that exists but has no last_update_time key must not crash."""
    fake_store["data"] = {"some_other_field": "value"}
    assert state_store.read_cursor(_settings()) is None


def test_write_retries_transient_failures_then_succeeds(monkeypatch, fake_store):
    attempts = {"n": 0}
    real_client = state_store._get_firestore_client(_settings())

    class _FlakyOnceDocRef:
        def set(self, data, merge=True):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("transient Firestore write failure")
            real_client.collection("x").document("y").set(data, merge=merge)

    class _FlakyOnceClient:
        def collection(self, name):
            return type("_C", (), {"document": staticmethod(lambda doc_id: _FlakyOnceDocRef())})()

    monkeypatch.setattr(state_store, "_get_firestore_client", lambda settings: _FlakyOnceClient())
    monkeypatch.setattr(state_store.time, "sleep", lambda seconds: None)

    state_store.write_cursor(_settings(), "2026-09-17T10:00:00Z")
    assert attempts["n"] == 2
    assert fake_store["data"]["last_update_time"] == "2026-09-17T10:00:00Z"


def test_write_raises_after_exhausting_all_retries(monkeypatch, fake_store):
    class _AlwaysFailsDocRef:
        def set(self, data, merge=True):
            raise RuntimeError("persistent Firestore write failure")

    class _AlwaysFailsClient:
        def collection(self, name):
            return type("_C", (), {"document": staticmethod(lambda doc_id: _AlwaysFailsDocRef())})()

    monkeypatch.setattr(state_store, "_get_firestore_client", lambda settings: _AlwaysFailsClient())
    monkeypatch.setattr(state_store.time, "sleep", lambda seconds: None)

    with pytest.raises(RuntimeError, match="persistent Firestore write failure"):
        state_store.write_cursor(_settings(), "2026-09-17T10:00:00Z")
