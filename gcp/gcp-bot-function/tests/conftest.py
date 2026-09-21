import pathlib
import sys

_APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))
