import pathlib
import sys

_INGEST_ROOT = pathlib.Path(__file__).resolve().parents[2] / "bot-ingest-function"
if str(_INGEST_ROOT) not in sys.path:
    sys.path.insert(0, str(_INGEST_ROOT))
