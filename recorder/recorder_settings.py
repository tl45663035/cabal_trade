import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "tools"))

import record_action as ra

_EXTRA = json.loads((HERE / "settings.json").read_text(encoding="utf-8"))
for _key, _value in _EXTRA["recorder"].items():
    ra.R.setdefault(_key, _value)
for _key, _value in _EXTRA["win"].items():
    ra.W.setdefault(_key, _value)
