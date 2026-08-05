"""Did the price floors actually hold, on the screens the script really saw?

    py unit_tests\\floor_live_test.py

The three existing floor suites test floor BEHAVIOUR: given a market price and
a floor, is the floor honoured? They all passed while ITEM_PRICE_FLOORS carried
105,000,000 instead of the 110,000,000 that was asked for, because behaviour
was right and the number was wrong. Four VIP memberships went out at
109,999,999 before anyone noticed.

This suite asks the other question -- what price is actually on the screen --
against every frame the script recorded. It is the only check here that could
have caught that, and it needs no ground truth: the floor is in the code and
the price is in the picture.

A listing made BEFORE a floor was raised keeps its old price until the script
next relists it, so a frame is only held to a floor if it was recorded after
that floor landed AND after the run that would have relisted it. Older frames
are reported, never asserted -- they were legal when they were taken.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import trade  # noqa: E402

BASELINE = HERE / "baseline_rows.json"
CORPUS = HERE / "corpus"

# When ITEM_PRICE_FLOORS last changed, from git. A frame older than this was
# taken under a different rule and cannot be judged by this one.
#
# UPDATE THIS WHENEVER A FLOOR MOVES. If it is left stale the suite judges
# frames against a floor that was not in force when they were taken, and
# reports violations that were legal at the time.
#   (this commit)  2026-08-04 21:20  Force Gem Package (x400) -> 180,000,000
#   faee956  2026-08-04 08:19:43 -0700  VIP floor -> 104,000,000
#   dfdd426  2026-08-04 07:45:15 -0700  VIP floor -> 110,000,000
FLOORS_CURRENT_FROM = datetime(2026, 8, 4, 21, 20, 0)
# RAISING a floor does not reprice what is already listed: those rows keep the
# old price until the script next relists them, which takes a cycle or two. So
# a violation only counts once the script has had time to act on it. (Lowering
# a floor cannot create a violation at all -- everything listed is above it.)
RELIST_GRACE_MINUTES = 45
# Below this many judged frames the suite is asserting almost nothing, which
# looks identical to passing. Say so instead: a green tick over an empty set is
# the failure mode this whole exercise exists to remove.
MIN_JUDGED_FRAMES = 20

failures: list[str] = []
checks = 0


def check(name: str, ok: bool, detail: str = "") -> bool:
    global checks
    checks += 1
    print(("[  ok  ] " if ok else "[ FAIL ] ") + name
          + ("" if ok or not detail else f"\n           {detail}"))
    if not ok:
        failures.append(f"{name}: {detail}")
    return ok


def main() -> int:
    if not BASELINE.exists():
        print("no baseline_rows.json yet - nothing to check.")
        return 0

    data = json.loads(BASELINE.read_text())
    floors = [(token, label, floor)
              for token, label, floor in trade.ITEM_PRICE_FLOORS]
    print("floors in force:")
    for _token, label, floor in floors:
        print(f"  {label:44} {floor:>14,}")

    changed_at = FLOORS_CURRENT_FROM.timestamp()
    settled_at = changed_at + RELIST_GRACE_MINUTES * 60
    seen = {label: [] for _t, label, _f in floors}
    violations = {label: [] for _t, label, _f in floors}
    pending = {label: [] for _t, label, _f in floors}
    old_below = {label: 0 for _t, label, _f in floors}
    judged = settled = ignored = 0

    for frame, rows in data.items():
        path = CORPUS / frame
        if not path.exists():
            continue
        stamp = path.stat().st_mtime
        recent = stamp >= changed_at
        is_settled = stamp >= settled_at
        judged += recent
        settled += is_settled
        ignored += not recent
        for r in rows:
            if r["action"] not in ("change", "receive"):
                continue
            price = r["price"]
            if price is None:
                continue
            floor = trade.item_price_floor(r["name"] or "")
            if not floor:
                continue
            # Which configured floor is this? item_price_floor resolves the
            # name; match it back to a label for reporting.
            label = next((lab for _t, lab, f in floors if f == floor),
                         r["name"])
            seen[label].append(price)
            if price < floor:
                where = (f"{frame} row {r['index']}: {r['name']!r} at "
                         f"{price:,} < floor {floor:,}")
                if is_settled:
                    violations[label].append(where)
                elif recent:
                    pending[label].append(where)
                else:
                    old_below[label] += 1

    print(f"\nframes recorded since the floors last changed: {judged:,}")
    print(f"  ...of those, past the {RELIST_GRACE_MINUTES}-minute relist "
          f"grace and therefore asserted on: {settled:,}")
    print(f"frames too old to judge (reported only): {ignored:,}")

    # A suite that asserts over an empty set passes for the wrong reason -- but
    # failing for it is worse. Changing a floor makes `judged` zero by
    # construction: no frame can postdate a change that was made seconds ago.
    # Hard-failing there paints the regression red for a reason that is not a
    # defect, until the game happens to be run again -- and a suite that is red
    # for a non-reason is one people learn to skip past, which is exactly how
    # the failpaths harness came to be hiding a dead suite this morning.
    #
    # So it is reported unmissably and does not gate. What DOES gate is a
    # violation, and those are asserted below on whatever data exists.
    if judged < MIN_JUDGED_FRAMES:
        print()
        print("  " + "!" * 66)
        print(f"  !! only {judged} frame(s) postdate the last floor change, so "
              f"the floor")
        print(f"  !! assertions below are checking almost nothing.")
        print(f"  !! Floors last changed: {FLOORS_CURRENT_FROM}")
        print(f"  !! Either the corpus is stale, or a floor has just been "
              f"changed and")
        print(f"  !! the script has not run since. Re-run this after a live "
              f"cycle.")
        print("  " + "!" * 66)
    if not settled:
        print(f"[ note ] no frame is past the {RELIST_GRACE_MINUTES}-minute "
              f"grace yet, so violations below are REPORTED, not asserted -- "
              f"the script has not had time to reprice what was already "
              f"listed.")

    print("\n--- the assertion: nothing below its floor, once settled ---")
    for _token, label, floor in floors:
        bad = violations[label]
        detail = ""
        if bad:
            detail = (f"{len(bad)} row(s) below {floor:,}; first 5:\n"
                      "           " + "\n           ".join(bad[:5]))
        check(f"{label} never below {floor:,}", not bad, detail)
        if pending[label]:
            print(f"[ note ] {label}: {len(pending[label])} row(s) still "
                  f"below the floor inside the grace window, e.g. "
                  f"{pending[label][0]}")

    print("\n--- what those items are actually listed at ---")
    for _token, label, floor in floors:
        prices = seen[label]
        if not prices:
            print(f"  {label}: not seen in any frame")
            continue
        lo, hi = min(prices), max(prices)
        at_floor = sum(1 for p in prices if p == floor)
        below = old_below[label]
        print(f"  {label}:")
        print(f"      {len(prices):,} sighting(s), {lo:,} .. {hi:,}")
        print(f"      exactly at the floor: {at_floor:,}")
        if below:
            print(f"      below it on OLDER frames (legal then): {below:,}")

    print(f"\n{'-' * 70}")
    print(f"{checks} floor(s) checked against real screens, "
          f"{len(failures)} FAILED")
    for line in failures:
        print(f"  FAIL {line}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
