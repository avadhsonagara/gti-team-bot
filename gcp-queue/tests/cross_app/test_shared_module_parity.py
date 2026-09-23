"""
Shared module byte-parity validation between ingest and worker Cloud
Functions.

Ensures that duplicated shared infrastructure files (logging_config,
observability, teams/activity, teams/context, teams/bot_client) remain
exactly byte-identical across both independent code trees — there is no
shared package between them (each is zipped and deployed independently by
terraform/main.tf), so this has to be enforced by a test instead of the
Python import system.

Note: PLACEHOLDER_TEXT living in each side's own constants.py is
deliberately NOT covered here (constants.py isn't fully identical between
the two — see its own comment) — that specific constant's byte-identity is
covered directly by grepping both files below.
"""
import pathlib

import pytest

_GCP_QUEUE_ROOT = pathlib.Path(__file__).resolve().parents[2]
_INGEST_APP = _GCP_QUEUE_ROOT / "bot-ingest-function" / "app"
_WORKER_APP = _GCP_QUEUE_ROOT / "bot-worker-function" / "app"

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


def test_placeholder_text_constant_is_identical_between_both_apps():
    ingest_constants = (_INGEST_APP / "constants.py").read_text()
    worker_constants = (_WORKER_APP / "constants.py").read_text()

    ingest_line = next(line for line in ingest_constants.splitlines() if line.startswith("PLACEHOLDER_TEXT"))
    worker_line = next(line for line in worker_constants.splitlines() if line.startswith("PLACEHOLDER_TEXT"))

    assert ingest_line == worker_line, (
        "PLACEHOLDER_TEXT has drifted between the two apps' constants.py — "
        "bot-worker-function's is_placeholder_message() matches on this "
        "exact string to exclude the bot's own placeholder from thread "
        "context, so it must stay byte-identical to what bot-ingest-function "
        "actually posts."
    )
