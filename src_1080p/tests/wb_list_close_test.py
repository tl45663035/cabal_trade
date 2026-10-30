import contextlib
import io
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "cang"
os.chdir(SRC)
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(SRC.parent / "tools"))
sys.argv = ["dungeon.py", "--config", "cang"]
import no_input
TRIPPED = no_input.arm()
import world_boss as wb

fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


@contextlib.contextmanager
def patched(*changes):
    saved = [(obj, name, getattr(obj, name)) for obj, name, _value in changes]
    try:
        for obj, name, value in changes:
            setattr(obj, name, value)
        yield
    finally:
        for obj, name, value in saved:
            setattr(obj, name, value)


def relog(moves, list_open):
    screen = {"list": False, "escapes": 0, "relogs": []}

    def quick_relog(channel, verbose=True):
        screen["relogs"].append(channel)
        screen["list"] = True
        if channel not in moves:
            raise wb.NoMoveDialog("no Move Channel dialog within 3s")
        screen["list"] = False

    def escape(vk):
        screen["escapes"] += 1
        if screen["escapes"] > list_open:
            screen["list"] = False

    with patched((wb, "quick_relog", quick_relog),
                 (wb, "listed", lambda: {4: (835, 531)} if screen["list"] else {}),
                 (wb, "menu_up", lambda: False),
                 (wb, "tap_key", escape),
                 (wb, "ESCAPE_GAP", 0.0),
                 (wb, "key_free", lambda: True)), \
            contextlib.redirect_stdout(io.StringIO()):
        try:
            wb.relog_unless_there(4)
        except RuntimeError as error:
            return screen, error
    return screen, None


screen, error = relog(moves=[], list_open=0)
check("already in Channel 4: the list left open by the double-click is shut",
      not screen["list"] and screen["escapes"] == 1 and error is None, str(screen))
screen, error = relog(moves=[4], list_open=0)
check("a real move presses no Escape", screen["escapes"] == 0 and error is None, str(screen))
screen, error = relog(moves=[], list_open=99)
check("a list that will not shut stops after the Escape tries",
      error is not None and screen["escapes"] == wb.ESCAPE_TRIES, str(error))

check("no input reached the game", not TRIPPED, str(TRIPPED))
print(f"{fails} failure(s)")
sys.exit(1 if fails else 0)
