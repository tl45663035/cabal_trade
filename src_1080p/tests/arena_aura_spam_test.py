import contextlib
import io
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["dungeon.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import calibration
import dungeon

checks = 0
failures = []


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


USE = ("use", "aura")
TARGET = dungeon.KEYS[dungeon.TARGET_KEY]
SHORT = 2 * dungeon.LOOP_GAP
done = []
saved = (dungeon.right_click, dungeon.use_point, dungeon.tap,
         dungeon.focus_game, dungeon.click_round, calibration.snap,
         dict(dungeon.AURA_DUE))
dungeon.right_click = lambda x, y, settle: done.append(("click", (x, y)))
dungeon.use_point = lambda name, settle: done.append(("use", name))
dungeon.tap = lambda vk, hold=None: done.append(("tap", vk))
dungeon.focus_game = lambda *a, **k: True
calibration.snap = lambda *a, **k: None
real_click_round = dungeon.click_round


def spam(target):
    done.clear()
    dungeon.AURA_DUE.update(at=time.monotonic(), target=target,
                            since=time.monotonic(), **{"for": SHORT})
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        dungeon.aura_when_due(True)
    return said.getvalue()


try:
    check(dungeon.setting("chaos_arena", "aura_for") == 10.0
          and dungeon.setting("chaos_arena", "aura_target") is False,
          f"a Chaos Arena spams the aura for 10s with no target key, got "
          f"{dungeon.setting('chaos_arena', 'aura_for')} and "
          f"{dungeon.setting('chaos_arena', 'aura_target')}")
    others = {name: dungeon.setting(name, "aura_target")
              for name in dungeon.DUNGEONS if name != "chaos_arena"}
    check(dungeon.AURA_TARGET is True and all(others.values()),
          f"every other dungeon keeps the target key after each aura click, "
          f"got {dungeon.AURA_TARGET} and {others}")
    for machine in ("bot", "trung", "cang"):
        own = json.loads((SRC / "configs" / f"{machine}.json").read_text(
            encoding="utf-8")).get("dungeon", {}).get("dungeons", {}).get(
            "chaos_arena", {})
        check(not {"aura_for", "aura_target"} & set(own),
              f"{machine}.json leaves the arena's aura to config.json, got {own}")

    dungeon.click_round = lambda names, since: dungeon.ENDED.wait(
        dungeon.LOOP_GAP)
    said = io.StringIO()
    before = time.monotonic()
    with contextlib.redirect_stdout(said):
        dungeon.run_sequence("chaos_arena", "CA5", {}, True, ["bm_aura"], False)
    due = dict(dungeon.AURA_DUE)
    check(due["for"] == 10.0 and due["target"] is False and due["after"] == 80.0
          and due["at"] is not None
          and 0.0 <= due["at"] - before - due["after"] < SHORT,
          f"the arena's bm_aura step sets the aura 80s on, spammed 10s with no "
          f"target key, got {due}")
    check(f"right-clicked over and over for 10s with no {dungeon.TARGET_KEY}"
          in said.getvalue(),
          f"the bm_aura step says the spam has no target key: {said.getvalue()!r}")
    dungeon.click_round = real_click_round

    said = spam(False)
    uses = [entry for entry in done if entry == USE]
    check(len(uses) >= 2 and len(uses) == len(done),
          f"the arena's spam uses only the aura, got {done}")
    check(f"aura {dungeon.slot_way('aura')}" in said
          and "after each" not in said,
          f"the arena's spam line names no target key: {said!r}")

    said = spam(True)
    check(len(done) >= 4 and done[0::2] == [USE] * (len(done) // 2)
          and done[1::2] == [("tap", TARGET)] * (len(done) // 2),
          f"the other dungeons' spam taps the target key after each click, "
          f"got {done}")
    check(f"{dungeon.TARGET_KEY} after each" in said,
          f"the other dungeons' spam line names the target key: {said!r}")

    done.clear()
    dungeon.AURA_DUE.update(at=time.monotonic(), target=False,
                            since=time.monotonic(), **{"for": SHORT})
    with contextlib.redirect_stdout(io.StringIO()):
        dungeon.click_round(dungeon.BM_LOOP, time.monotonic())
    spammed = 0
    while spammed < len(done) and done[spammed] == USE:
        spammed += 1
    expected = []
    for entry in dungeon.BM_LOOP:
        if entry == dungeon.DEFENSIVE:
            expected.append(("click", dungeon.DEFENSIVE_BUFFS[0][1]))
        elif entry in dungeon.CLICK_POINTS:
            expected.append(("use", entry))
        else:
            expected.append(("tap", dungeon.KEYS[entry]))
    check(spammed >= 2 and done[spammed:] == expected
          and ("tap", TARGET) not in done,
          f"the arena spams the aura, then plays its whole normal round "
          f"{dungeon.BM_LOOP} with the aura in it, got {done}")
finally:
    (dungeon.right_click, dungeon.use_point, dungeon.tap, dungeon.focus_game,
     dungeon.click_round, calibration.snap, original) = saved
    dungeon.AURA_DUE.clear()
    dungeon.AURA_DUE.update(original)
    dungeon.OVER.clear()
    dungeon.ENDED.clear()

check(not TRIPPED, f"tests sent input: {TRIPPED}")
print(f"{checks} checks, {len(failures)} failed")
for f in failures:
    print("  FAIL:", f)
sys.exit(1 if failures else 0)
