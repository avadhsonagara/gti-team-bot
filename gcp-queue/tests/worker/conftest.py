import pathlib
import sys

_WORKER_ROOT = pathlib.Path(__file__).resolve().parents[2] / "bot-worker-function"
if str(_WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKER_ROOT))
