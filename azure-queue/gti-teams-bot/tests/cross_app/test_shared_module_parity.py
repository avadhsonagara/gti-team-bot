"""
Shared module byte-parity validation between ingest and worker function applications.

Ensures that duplicated shared infrastructure files (logging_config, observability,
teams/activity, teams/context, and teams/bot_client) remain exactly byte-identical
across both independent Function App code trees.
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
