"""
Guards finding #10 (code duplication): bot-ingest-function and
bot-worker-function each deploy independently (separate requirements.txt,
separate code.zip, no shared installable package between them — see
main.bicep's ingestCodeZipUrl/workerCodeZipUrl), so restructuring these into
a real shared package would mean either symlinks (which `git archive` does
not follow when building each app's code.zip) or a new multi-file zip-build
step neither app's deployment currently has.

Given that, the safer fix for the actual risk described in finding #10 — "an
engineer alters ... in one app without updating the other" — is to make that
drift impossible to miss silently: this test fails the moment any of these
byte-identical files diverge, so a change lands as an intentional, reviewed
edit to both copies rather than as an unnoticed contract break.

If a file listed here is EVER intentionally allowed to diverge, remove it
from this list rather than deleting the test.
"""
import pathlib

import pytest

_GTI_TEAMS_BOT_ROOT = pathlib.Path(__file__).resolve().parents[2]
_INGEST_APP = _GTI_TEAMS_BOT_ROOT / "bot-ingest-function" / "app"
_WORKER_APP = _GTI_TEAMS_BOT_ROOT / "bot-worker-function" / "app"

_SHARED_RELATIVE_PATHS = [
    "logging_config.py",
    "observability.py",
    "teams/activity.py",
    "teams/context.py",
    "teams/bot_client.py",
]


@pytest.mark.parametrize("relative_path", _SHARED_RELATIVE_PATHS)
def test_shared_module_is_identical_between_both_apps(relative_path: str):
    ingest_file = _INGEST_APP / relative_path
    worker_file = _WORKER_APP / relative_path
    assert ingest_file.is_file(), f"Expected {ingest_file} to exist."
    assert worker_file.is_file(), f"Expected {worker_file} to exist."
    assert ingest_file.read_text() == worker_file.read_text(), (
        f"app/{relative_path} has drifted between bot-ingest-function and "
        "bot-worker-function — since there's no shared package between "
        "them, this file's content must be kept identical by hand. Apply "
        "whatever change you just made to BOTH copies."
    )
