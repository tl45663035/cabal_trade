"""Structural and safety invariants over every row the reader has ever produced.

    py unit_tests\\invariants_test.py

Why this exists
---------------
The corpus suite compares read_rows against values recorded BY read_rows, so a
reader that was wrong when the frame was recorded produces a green suite. An
audit of ~2,895 assertions found 91.8% bit-identically circular and ZERO
independent. This suite is the independent kind: it asserts PROPERTIES that
must hold whatever the pixels said, so no recorded value can excuse a
violation.

It reads baseline_rows.json rather than re-OCRing the corpus, so it costs
about a second and does not compete with a live run for cores.

The invariants are chosen for consequence, not tidiness. The ones that matter
most are about the CLICK TARGET: read_rows hands `change` straight to click(),
and a click that lands outside its row cancels the wrong listing, while one
outside the table lands in the game world and moves the character or an item.
"""

import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import trade  # noqa: E402

BASELINE = HERE / "baseline_rows.json"

VALID_ACTIONS = {"change", "receive", "register"}
# The click column is found per row by OCR, so its x wobbles a little between
# rows of the same frame. This is the widest wobble that still cannot reach a
# neighbouring column: the Function column's buttons sit ~1116 and the nearest
# other clickable thing (a dialog button) is past x=1200.
CLICK_X_SPREAD = 60
# A screen this side of absurd. Purely a "did the arithmetic run away" bound.
MAX_COORD = 10_000
# Row bands are derived by scaling a reference pitch and rounding to whole
# pixels, so adjacent bands can share an edge. Measured: 152 of 30,124 adjacent
# pairs overlap, every one of them by exactly 1px, and all of them in frames
# whose pitch rounded to 77px instead of 79px.
#
# That cannot move a click -- the click target is the band's centre, and the
# bands are only otherwise used to crop text for OCR, where one shared row of
# pixels changes nothing. A LARGE overlap is a different animal: it means two
# rows are being read from the same pixels, so this still asserts, just not at
# a threshold that only catches rounding.
MAX_BAND_OVERLAP = 2
# How far off-centre a click may sit within its row, as a fraction of the row
# height. A click near the boundary risks the divider between rows rather than
# the button.
MAX_CLICK_OFFSET = 0.40

failures: list[str] = []
checks = 0


def check(name: str, ok: bool, detail: str = "") -> bool:
    global checks
    checks += 1
    if ok:
        print(f"[  ok  ] {name}")
    else:
        print(f"[ FAIL ] {name}\n           {detail}")
        failures.append(f"{name}: {detail}")
    return ok


def main() -> int:
    if not BASELINE.exists():
        print("no baseline_rows.json yet - run baseline_rows.py save first.")
        print("(a fresh checkout has no corpus, so there is nothing to assert)")
        return 0

    data = json.loads(BASELINE.read_text())
    frames = sorted(data)
    rows_total = sum(len(v) for v in data.values())
    print(f"{len(frames):,} frames, {rows_total:,} rows\n")
    if not rows_total:
        print("baseline holds no rows - nothing to assert")
        return 0

    # -- collect every violation, then assert on the counts -----------------
    bad_action, bad_index, bad_band, overlap = [], [], [], []
    click_outside_band, click_absurd, click_spread = [], [], []
    register_priced, live_unnamed, empty_named = [], [], []
    bad_price, bad_qty, too_many = [], [], []
    click_off_centre, ragged_height = [], []
    actions = Counter()
    pitches = Counter()
    overlaps = Counter()
    unread_price = unread_qty = live_rows = 0

    for frame in frames:
        rows = data[frame]
        if not rows:
            continue
        if len(rows) > trade.EXPECTED_ROWS:
            too_many.append(f"{frame}: {len(rows)} rows")

        indices = [r["index"] for r in rows]
        if indices != list(range(1, len(rows) + 1)):
            bad_index.append(f"{frame}: {indices}")

        xs = []
        for r in rows:
            actions[r["action"]] += 1
            if r["action"] not in VALID_ACTIONS:
                bad_action.append(f"{frame} row {r['index']}: {r['action']!r}")

            top, bottom = r["top"], r["bottom"]
            if not (isinstance(top, int) and isinstance(bottom, int)
                    and top < bottom):
                bad_band.append(f"{frame} row {r['index']}: {top}..{bottom}")
                continue

            cx, cy = r["change"]
            xs.append(cx)
            # The click must land inside the row it names. Outside it, the
            # cancel hits a different listing -- and every downstream identity
            # check would confirm the WRONG row happily.
            if not (top <= cy <= bottom):
                click_outside_band.append(
                    f"{frame} row {r['index']}: click y={cy} outside "
                    f"{top}..{bottom}")
            if not (0 < cx < MAX_COORD and 0 < cy < MAX_COORD):
                click_absurd.append(f"{frame} row {r['index']}: {(cx, cy)}")
            height = bottom - top
            if height and abs(cy - (top + bottom) / 2) > height * MAX_CLICK_OFFSET:
                click_off_centre.append(
                    f"{frame} row {r['index']}: click y={cy} is "
                    f"{abs(cy - (top + bottom) / 2):.0f}px off the centre of a "
                    f"{height}px row")

            price, qty, name = r["price"], r["qty"], (r["name"] or "")
            if r["action"] == "register":
                # An empty slot has nothing listed in it.
                if price is not None or qty is not None:
                    register_priced.append(
                        f"{frame} row {r['index']}: empty slot priced "
                        f"{price!r} x{qty!r}")
            else:
                if not name.strip() or name.strip() == "(empty)":
                    live_unnamed.append(
                        f"{frame} row {r['index']}: {r['action']} row named "
                        f"{name!r}")
            if name.strip() == "(empty)" and r["action"] != "register":
                empty_named.append(f"{frame} row {r['index']}: {r['action']}")

            if price is not None and (not isinstance(price, int) or price <= 0):
                bad_price.append(f"{frame} row {r['index']}: price {price!r}")
            if qty is not None and (not isinstance(qty, int) or qty < 0):
                bad_qty.append(f"{frame} row {r['index']}: qty {qty!r}")
            if r["action"] in ("change", "receive"):
                live_rows += 1
                unread_price += price is None
                unread_qty += qty is None

        heights = [r["bottom"] - r["top"] for r in rows
                   if isinstance(r["top"], int) and isinstance(r["bottom"], int)]
        if heights and max(heights) - min(heights) > MAX_BAND_OVERLAP + 1:
            ragged_height.append(
                f"{frame}: row heights span {min(heights)}..{max(heights)}")

        for a, b in zip(rows, rows[1:]):
            over = a["bottom"] - b["top"]
            if over > 0:
                overlaps[over] += 1
            if over > MAX_BAND_OVERLAP:
                overlap.append(
                    f"{frame}: row {a['index']} ends {a['bottom']}, "
                    f"row {b['index']} starts {b['top']} ({over}px)")
            pitches[b["top"] - a["top"]] += 1

        if xs and max(xs) - min(xs) > CLICK_X_SPREAD:
            click_spread.append(
                f"{frame}: click x spans {min(xs)}..{max(xs)} "
                f"({max(xs) - min(xs)}px)")

    def report(title: str, bad: list[str]) -> bool:
        ok = not bad
        detail = ""
        if bad:
            detail = f"{len(bad)} violation(s); first 5:\n           " + \
                "\n           ".join(bad[:5])
        return check(title, ok, detail)

    print("--- the click target: what a wrong answer costs is a wrong cancel ---")
    report("every click lands inside the row it names", click_outside_band)
    report("every click sits near its row's centre, not on a divider",
           click_off_centre)
    report("no click coordinate is absurd", click_absurd)
    report("all of a frame's clicks share one column", click_spread)

    print("\n--- table structure ---")
    report("row indices are 1..N with no gaps", bad_index)
    report("every row band is top < bottom", bad_band)
    report(f"no two rows overlap by more than {MAX_BAND_OVERLAP}px", overlap)
    report("a frame's rows are all the same height", ragged_height)
    report("no frame exceeds EXPECTED_ROWS", too_many)
    report("every action is one of change/receive/register", bad_action)

    print("\n--- what a row of each kind may hold ---")
    report("empty slots carry no price or quantity", register_priced)
    report("live rows are named", live_unnamed)
    report("'(empty)' appears only on empty slots", empty_named)
    report("prices are positive integers or absent", bad_price)
    report("quantities are non-negative integers or absent", bad_qty)

    print("\n--- observed, for context ---")
    for action, n in actions.most_common():
        print(f"  {n:7,}  {action}")
    common = pitches.most_common(4)
    print(f"  row pitch (top-to-top): "
          + ", ".join(f"{p}px x{n:,}" for p, n in common))
    if overlaps:
        print("  band overlaps (all tolerated below the threshold): "
              + ", ".join(f"{px}px x{n:,}" for px, n in
                          sorted(overlaps.items())))
    if live_rows:
        print(f"  live rows with an unread PRICE: {unread_price:,} / "
              f"{live_rows:,} ({unread_price / live_rows:.2%})")
        print(f"  live rows with an unread QTY  : {unread_qty:,} / "
              f"{live_rows:,} ({unread_qty / live_rows:.2%})")

    print(f"\n{'-' * 70}")
    print(f"{checks} invariant(s) over {rows_total:,} rows, {len(failures)} FAILED")
    for line in failures:
        print(f"  FAIL {line}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
