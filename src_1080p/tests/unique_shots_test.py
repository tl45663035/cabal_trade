"""Every unique drop's screenshot goes to its own folder, which nothing clears."""
import datetime
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ["CABAL_CONFIG"] = "cang"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

import calibration
import dungeon

failures = []


def check(name, ok, detail=""):
    print(("ok  " if ok else "FAIL"), name, "" if ok else detail)
    if not ok:
        failures.append(name)


check("the folder is logs/unique_drops, beside the drop list",
      dungeon.UNIQUE_SHOTS == calibration.LOG_DIR / "unique_drops", dungeon.UNIQUE_SHOTS)
frames = Path(calibration.FRAME_DIR).resolve()
check("it is outside the debug frames that get pruned",
      frames not in dungeon.UNIQUE_SHOTS.resolve().parents, dungeon.UNIQUE_SHOTS)

root = Path(tempfile.mkdtemp())
dungeon.UNIQUE_SHOTS = root / "unique_drops"
dungeon.UNIQUE_FILE = root / "unique_drops.jsonl"
dungeon.report_at = lambda: "11:18:52"
dungeon.internet_now = lambda: datetime.datetime(2026, 11, 4, 17, 18, 52,
                                                 tzinfo=datetime.timezone.utc)
calibration.snap = lambda *a, **k: None
owner = {"day": "2026-11-04", "name": "Edge of Phantom", "label": "eop #20"}
image = Image.new("RGB", (64, 36), (10, 20, 30))
try:
    first = dungeon.note_unique(owner, "slot Extender(High", image, False)
    check("the file name has the online day and time, the run and the item",
          first.name == "2026-11-04_111852_eop_20_slot_Extender(High.png", first)
    check("it is the full image", Image.open(first).size == (64, 36))
    second = dungeon.note_unique(owner, "slot Extender(High", image, False)
    check("a second drop in the same second does not overwrite it",
          second != first and first.exists() and second.exists(), second)
    odd = dungeon.note_unique(owner, 'a/b:c?"[d]', image, False)
    check("odd characters from the chat reading still make a valid file", odd.exists(), odd)
    lines = [json.loads(line) for line in dungeon.UNIQUE_FILE.read_text().splitlines()]
    check("each drop is still in the list",
          len(lines) == 3 and lines[0]["run"] == "eop #20", lines)

    dungeon.internet_now = lambda: None
    offline = dungeon.note_unique(owner, "Offline", image, False)
    check("no online time: the shot is still kept, marked as such, with the day",
          offline.name == "2026-11-04_no-online-time_eop_20_Offline.png", offline)

    def no_network():
        raise OSError("no network")

    dungeon.internet_now = no_network
    failing = dungeon.note_unique(owner, "Failing", image, False)
    check("a clock that raises does not lose the shot", failing.exists(), failing)

    class Broken:
        def save(self, *a, **k):
            raise OSError("disk full")

    kept = dungeon.note_unique(owner, "Other", Broken(), False)
    lines = dungeon.UNIQUE_FILE.read_text().splitlines()
    check("a screenshot that cannot be saved does not lose the drop",
          kept is None and len(lines) == 6, (kept, len(lines)))
finally:
    shutil.rmtree(root, ignore_errors=True)

print("FAILED:" if failures else "all passed", failures or "")
sys.exit(1 if failures else 0)
