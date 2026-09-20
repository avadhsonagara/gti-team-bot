import pathlib
import sys

_RS_ALERTS_ROOT = pathlib.Path(__file__).resolve().parents[2] / "rs-alerts-function"
if str(_RS_ALERTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_RS_ALERTS_ROOT))
