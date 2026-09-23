"""
Tests for app/dedup_store.py's Pub/Sub messageId claim — the cross-instance
redelivery guard job_processor.py relies on (see its module docstring for
why an in-memory guard, like gcp/gcp-bot-function's _claim_activity, can't
work here: Cloud Functions gen2 can scale to multiple instances with
independent memory).
"""
from google.api_core import exceptions as gcloud_exceptions

from app import dedup_store


class _FakeDocRef:
    def __init__(self, existing_ids: set, doc_id: str):
        self._existing_ids = existing_ids
        self._doc_id = doc_id

    def create(self, data):
        if self._doc_id in self._existing_ids:
            raise gcloud_exceptions.AlreadyExists("already claimed")
        self._existing_ids.add(self._doc_id)


class _FakeCollection:
    def __init__(self, existing_ids: set):
        self._existing_ids = existing_ids

    def document(self, doc_id: str):
        return _FakeDocRef(self._existing_ids, doc_id)


class _FakeFirestoreClient:
    def __init__(self, existing_ids: set):
        self._existing_ids = existing_ids

    def collection(self, name: str):
        return _FakeCollection(self._existing_ids)


def test_first_claim_of_a_message_id_succeeds(monkeypatch):
    existing: set = set()
    monkeypatch.setattr(dedup_store, "_get_firestore_client", lambda cfg: _FakeFirestoreClient(existing))

    assert dedup_store.claim_message("msg-1") is True


def test_second_claim_of_the_same_message_id_fails(monkeypatch):
    existing: set = set()
    monkeypatch.setattr(dedup_store, "_get_firestore_client", lambda cfg: _FakeFirestoreClient(existing))

    assert dedup_store.claim_message("msg-1") is True
    assert dedup_store.claim_message("msg-1") is False


def test_different_message_ids_do_not_collide(monkeypatch):
    existing: set = set()
    monkeypatch.setattr(dedup_store, "_get_firestore_client", lambda cfg: _FakeFirestoreClient(existing))

    assert dedup_store.claim_message("msg-1") is True
    assert dedup_store.claim_message("msg-2") is True


def test_empty_message_id_always_proceeds(monkeypatch):
    # Nothing stable to key a claim on — should never block, and shouldn't
    # even need a Firestore client for it.
    monkeypatch.setattr(dedup_store, "_get_firestore_client", lambda cfg: (_ for _ in ()).throw(AssertionError("should not be called")))
    assert dedup_store.claim_message("") is True


def test_unreachable_firestore_fails_open(monkeypatch):
    """A missed dedup check is far less harmful than dropping a real job."""
    monkeypatch.setattr(dedup_store, "_get_firestore_client", lambda cfg: None)
    assert dedup_store.claim_message("msg-1") is True
