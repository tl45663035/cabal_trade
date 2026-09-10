
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import trade

BASELINE = HERE / "baseline_rows.json"
CORPUS = HERE / "corpus"

FLOORS_CURRENT_FROM = datetime(2026, 8, 4, 21, 20, 0)
RELIST_GRACE_MINUTES = 45
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

    resolved = 0
    for frame, rows in data.items():
        path = CORPUS / frame
        if not path.exists():
            continue
        resolved += 1
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

    check("the baseline resolves against the corpus", resolved > 0,
          f"0 of {len(data)} baselined frames are present in "
          f"{CORPUS}. The corpus has rotated away from this "
          f"baseline, so every check below is vacuous. Re-baseline "
          f"it, or point CORPUS at the frames it was taken from.")

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
