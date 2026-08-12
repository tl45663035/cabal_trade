"""The live config file: exported at run start, re-read every cycle.

Operator's request, 2026-08-12: "export all the flags into a json, then the
script reads it and update its param every cycle."

The rules that matter, and the reason for each:

  * A bad file NEVER stops the run. A file caught half-written by an editor is
    the normal case, not the exceptional one, and a run that dies because a
    save landed mid-read is worse than one that keeps its current values for
    another cycle.
  * Changes are ALL OR NOTHING. The knobs constrain each other --
    RESTOCK_TARGET against BUY_MAXIMUM, CHAOS_RESTOCK_AT_OR_BELOW_ROWS against
    CHAOS_ROWS -- so applying the valid half of an edit lands the run
    somewhere nobody asked for.
  * Applied BETWEEN cycles only. Every knob here is read many times inside a
    cycle; changing one halfway through prices a batch by two different rules.

Nothing here touches the game.
"""
import json
import os
import sys
import tempfile
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

_SCRATCH = _Path(tempfile.mkdtemp(prefix="cabal_cfg_test_"))
os.environ["CABAL_SALES_DB"] = str(_SCRATCH / "scratch.db")

import trade as m  # noqa: E402

CHECKS = FAILED = 0


def check(cond, what):
    global CHECKS, FAILED
    CHECKS += 1
    if not cond:
        FAILED += 1
        print(f"  FAIL  {what}")


# Point the module at a scratch file, never the operator's own.
m.LIVE_CONFIG_FILE = _SCRATCH / "config.json"

BASE = dict(CHAOS_ENABLED=True, CHAOS_ROWS=4,
            CHAOS_RESTOCK_AT_OR_BELOW_ROWS=2, CHAOS_BUY_QUANTITY=100,
            CHAOS_MARGIN_FLOOR=10_000, CHAOS_UNDERCUT=1,
            BUY_ENABLED=False, RESTOCK_TARGET=200, BUY_MAXIMUM=500,
            RESTOCK_AT_OR_BELOW_ROWS=1)


def reset():
    for k, v in BASE.items():
        setattr(m, k, v)


def write(**overrides):
    body = dict(BASE)
    body.update(overrides)
    m.LIVE_CONFIG_FILE.write_text(json.dumps(body), encoding="utf-8")


def write_raw(text):
    m.LIVE_CONFIG_FILE.write_text(text, encoding="utf-8")


print("live config")

# ------------------------------------------------------------- round trip ---
reset()
m.export_live_config(verbose=False)
body = json.loads(m.LIVE_CONFIG_FILE.read_text(encoding="utf-8"))
for name in m.LIVE_KNOBS:
    check(name in body, f"{name} must be exported")
check(body["CHAOS_ROWS"] == 4 and body["BUY_MAXIMUM"] == 500,
      "the export carries the values the run resolved to")

# TWO NAMES FOR ONE NUMBER IS A TRAP ONCE EXPORTED.
#
# CHAOS_MAX_ROW duplicated max(--relist-rows); BUY_TARGET was defined as
# BUY_MAXIMUM and set from the same --core-max. Inside the file that is untidy;
# in a config file the operator edits it is silently incoherent, because
# nothing stops the two being set apart and different code reads each one.
check("CHAOS_MAX_ROW" not in m.LIVE_KNOBS,
      "CHAOS_MAX_ROW must not be a knob: chaos_boundary() derives it from the "
      "rows the batch was asked for")
check("BUY_TARGET" not in m.LIVE_KNOBS,
      "BUY_TARGET must not be a knob: it was always BUY_MAXIMUM")
check(not hasattr(m, "BUY_TARGET") and not hasattr(m, "CHAOS_MAX_ROW"),
      "and neither should still exist as a module global to drift from")
check(m.apply_live_config(verbose=False) == [],
      "re-reading an unchanged file changes nothing")

# ---------------------------------------------------------------- applied ---
reset()
write(CHAOS_BUY_QUANTITY=200, CHAOS_MARGIN_FLOOR=25_000)
changed = m.apply_live_config(verbose=False)
check(sorted(changed) == ["CHAOS_BUY_QUANTITY", "CHAOS_MARGIN_FLOOR"],
      f"both edits reported, got {changed}")
check(m.CHAOS_BUY_QUANTITY == 200 and m.CHAOS_MARGIN_FLOOR == 25_000,
      "and both actually applied to the module")

# Turning buying on and off mid-run is the whole point.
reset()
write(BUY_ENABLED=True, CHAOS_ENABLED=False)
m.apply_live_config(verbose=False)
check(m.BUY_ENABLED is True and m.CHAOS_ENABLED is False,
      "the enable flags flip mid-run")

# ------------------------------------------------------- a bad file is safe ---
reset()
write_raw("{ this is not json,,,")
check(m.apply_live_config(verbose=False) == [],
      "malformed JSON changes nothing")
check(m.CHAOS_ROWS == 4,
      "and leaves the running values alone -- an editor's half-written save "
      "must not end a six-hour run")

reset()
m.LIVE_CONFIG_FILE.unlink()
check(m.apply_live_config(verbose=False) == [],
      "a missing file is not an error either")
write()

# ------------------------------------------------------- all or nothing ----
# RESTOCK_TARGET raised past a BUY_MAXIMUM that stays put: the pair is
# invalid, so NEITHER lands -- not even the unrelated, perfectly valid
# CHAOS_BUY_QUANTITY sharing the file.
reset()
write(RESTOCK_TARGET=900, CHAOS_BUY_QUANTITY=300)
check(m.apply_live_config(verbose=False) == [],
      "an invalid combination applies nothing")
check(m.RESTOCK_TARGET == 200 and m.CHAOS_BUY_QUANTITY == 100,
      "including the VALID edit in the same file -- half an edit lands the "
      "run somewhere nobody asked for")

# Raised together, it is legal.
reset()
write(RESTOCK_TARGET=900, BUY_MAXIMUM=1200)
check(sorted(m.apply_live_config(verbose=False))
      == ["BUY_MAXIMUM", "RESTOCK_TARGET"],
      "raising both together is valid and applies")

# Each relationship, stated as its own case.
for bad, why in (
    (dict(CHAOS_ROWS=99), "more chaos rows than the 30-row shop"),
    (dict(CHAOS_RESTOCK_AT_OR_BELOW_ROWS=9), "restock mark above the target"),
    (dict(RESTOCK_TARGET=900), "core min above core max"),
    (dict(CHAOS_BUY_QUANTITY=0), "a top-up of zero Cores"),
    (dict(CHAOS_ROWS=-1), "a negative count"),
    (dict(CHAOS_ROWS="four"), "a string where a number belongs"),
    (dict(CHAOS_ENABLED="yes"), "a string where a bool belongs"),
    (dict(CHAOS_ROWS=True), "a bool where a number belongs"),
):
    reset()
    write(**bad)
    check(m.apply_live_config(verbose=False) == [], f"rejected: {why}")

# ------------------------------------------------------------ where it runs ---
src = (_ROOT / "trade.py").read_text(encoding="utf-8-sig")
loop = src[src.index("def run_loop"):]
loop = loop[:loop.index("\ndef ", 10)]
check("export_live_config(" in loop,
      "the export must happen inside the run, after arguments are applied -- "
      "a config file that disagrees with its own run is worse than none")
check("apply_live_config(" in loop,
      "and the re-read must be in the loop")
check(loop.index("apply_live_config(") > loop.index("while time.monotonic()"),
      "the re-read belongs INSIDE the while loop, not once before it")
check(loop.index("stop_requested()") < loop.index("apply_live_config("),
      "the stop check comes first: a run being stopped should not spend a "
      "read on config it will never use")

print(f"live config: {CHECKS} checks, {FAILED} failed")


# ======================================================================
# RESTOCK PRIORITY
# ======================================================================
# Operator's order, 2026-08-12: "FCH -> UCU -> FCU -> FCHH. If any of them need
# supply, the higher priority go first."
#
# It matters because a restock often cannot finish everything it wants -- Alz
# runs out, rows run out, the breaker stops the run -- so this decides which
# stock is worth holding when only some of it can be.
print("restock priority")

WANT = ["Force Core(High)", "Upgrade Core (Ultimate)",
        "Force Core (Ultimate)", "Force Core(Highest)"]
SLOT = {v: k for k, v in m.FAVOURITE_SLOTS.items()}
EXPECT = [SLOT[n] for n in WANT]

check(m.in_restock_priority(m.managed_core_slots()) == EXPECT,
      f"full set restocks FCH, UCU, FCU, FCHH in that order; got "
      f"{[m.FAVOURITE_SLOTS[s] for s in m.in_restock_priority(m.managed_core_slots())]}")

# The order must survive SUBSETS -- only some Cores are short on any cycle.
for sub in ([SLOT["Force Core(Highest)"], SLOT["Upgrade Core (Ultimate)"]],
            [SLOT["Force Core (Ultimate)"], SLOT["Force Core(High)"]],
            [SLOT["Force Core(Highest)"], SLOT["Force Core(High)"]]):
    got = m.in_restock_priority(sub)
    ranked = sorted(sub, key=lambda s: EXPECT.index(s))
    check(got == ranked,
          f"subset {sub} must keep the priority, got {got} want {ranked}")

# ORDERED BY NAME, NOT BY SLOT. Slot order is what produced the OLD sequence,
# which was this one exactly reversed -- so a sort that happens to agree with
# slot numbers would pass a weaker test while meaning the wrong thing.
check(EXPECT != sorted(EXPECT),
      "the operator's order must not coincide with slot order, or this test "
      "proves nothing about which one is being used")
check(m.in_restock_priority(sorted(EXPECT)) == EXPECT,
      "a slot-sorted input must come back in PRIORITY order")

# An unnamed Core is restocked last rather than dropped.
check(m.in_restock_priority([SLOT["Upgrade Core (Ultimate)"], 99]) ==
      [SLOT["Upgrade Core (Ultimate)"], 99],
      "a Core with no priority entry sorts last, not out")

# Both places that choose a restock order must use it.
_src = (_ROOT / "trade.py").read_text(encoding="utf-8-sig")
for fn in ("slots_needing_restock", "restock_pass"):
    body = _src[_src.index(f"def {fn}"):]
    body = body[:body.index("\ndef ", 10)]
    check("in_restock_priority" in body,
          f"{fn} must order its slots by priority, or the two paths disagree "
          f"about which Core matters most")

print(f"restock priority: {CHECKS} checks total, {FAILED} failed")
sys.exit(1 if FAILED else 0)
