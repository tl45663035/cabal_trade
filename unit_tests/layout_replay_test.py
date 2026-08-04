"""A frame must carry the geometry it was READ under, and replay under it.

    py unit_tests\\layout_replay_test.py

The gap this closes
-------------------
Every search region is derived from LAYOUT, and Tesseract's sparse-text
segmentation is crop-dependent: hand it a crop one pixel different and it can
segment the same pixels differently. So replaying a recorded frame under a
different layout can legitimately produce a different answer, and comparing the
two is not a test of the reader -- it is an unfair comparison that reports the
reader as broken.

That is not theoretical. Calibration lands on origin (9,29) or (10,30) from OCR
jitter alone -- 11 times and 13 times across one day's runs -- and frames
recorded at one and replayed at the other shifted the NPC nameplate centre by
up to 5px, failing 4 of 378,764 corpus assertions.

The fix was to stamp each frame with its layout and restore it on replay. This
suite is that fix's own coverage, which it did not have: the fix was made and
shipped on the strength of the corpus run going green, and "the suite stopped
complaining" is exactly the evidence that has been wrong before.
"""

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from PIL import Image  # noqa: E402

import suite_corpus as sc  # noqa: E402
import trade as m  # noqa: E402

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


def layout_at(origin, scale=1.0, screen=(2560, 1440)):
    return m.Layout(screen=screen, origin=origin, scale=scale,
                    measured_from="test")


ORIGIN_A = (9, 29)
ORIGIN_B = (10, 30)


def section(title):
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


# ===========================================================================
section("1. the premise: a different layout really does move the crops")

# If this fails the whole fix is pointless, so it is asserted rather than
# assumed. These are the regions the coordinate readers actually search.
m.apply_layout(layout_at(ORIGIN_A))
regions_a = (m.TRADE_REGION, m.NPC_SEARCH_REGION)
m.apply_layout(layout_at(ORIGIN_B))
regions_b = (m.TRADE_REGION, m.NPC_SEARCH_REGION)

check("a one-pixel origin change moves the Trade region",
      regions_a[0] != regions_b[0],
      f"{regions_a[0]} vs {regions_b[0]} -- if these matched, replaying under "
      f"the wrong layout would be harmless and no fix would be needed")
check("...and the NPC search region with it",
      regions_a[1] != regions_b[1],
      f"{regions_a[1]} vs {regions_b[1]}")

m.apply_layout(layout_at((100, 200), scale=1.5))
big = m.TRADE_REGION
check("a scale change moves them too", big != regions_b[0],
      f"{big} vs {regions_b[0]}")


# ===========================================================================
section("2. record() stamps the layout it was reading under")

shot = Image.new("RGB", (200, 120))
for origin, scale in ((ORIGIN_A, 1.0), (ORIGIN_B, 1.0), ((100, 200), 1.25)):
    with tempfile.TemporaryDirectory() as tmp:
        saved = (m.RECORD_DIR, m.RECORD_ENABLED, m._record_seq, m.LAYOUT)
        try:
            m.apply_layout(layout_at(origin, scale))
            m.RECORD_DIR = Path(tmp)
            m.RECORD_ENABLED = True
            m._record_seq = 0
            m.record("test.frame", shot, centre="(1, 2)")
            lines = [l for l in (Path(tmp) / "run_index.jsonl")
                     .read_text(encoding="utf-8").splitlines() if l.strip()]
            entry = json.loads(lines[-1])
        finally:
            m.RECORD_DIR, m.RECORD_ENABLED, m._record_seq = saved[:3]
            m.apply_layout(saved[3])

    check(f"origin {origin} scale {scale}: the entry carries a layout",
          "layout" in entry, f"keys: {sorted(entry)}")
    got = entry.get("layout") or {}
    check(f"origin {origin} scale {scale}: origin recorded correctly",
          tuple(got.get("origin", ())) == tuple(origin),
          f"recorded {got.get('origin')!r}")
    check(f"origin {origin} scale {scale}: scale recorded correctly",
          abs(float(got.get("scale", -1)) - scale) < 1e-6,
          f"recorded {got.get('scale')!r}")
    check(f"origin {origin} scale {scale}: context still reaches the index",
          entry.get("centre") == "(1, 2)",
          f"entry: {entry!r} -- the layout must not displace what the caller "
          f"passed")
    check(f"origin {origin} scale {scale}: the reserved keys survive",
          entry.get("label") == "test.frame" and "file" in entry
          and "at" in entry, f"entry: {entry!r}")


# ===========================================================================
section("3. a recorded layout round-trips to the same crops")

for origin, scale in ((ORIGIN_A, 1.0), (ORIGIN_B, 1.0), ((100, 200), 1.25)):
    m.apply_layout(layout_at(origin, scale))
    wanted = (m.TRADE_REGION, m.NPC_SEARCH_REGION, m.POPUP_REGION)
    spec = {"origin": list(origin), "scale": scale, "screen": [2560, 1440]}

    m.apply_layout(layout_at((1, 1), 2.0))          # somewhere else entirely
    restored = sc._restore_layout({"layout": spec})
    got = (m.TRADE_REGION, m.NPC_SEARCH_REGION, m.POPUP_REGION)

    check(f"origin {origin} scale {scale}: reported as replayable",
          restored is True, f"got {restored!r}")
    check(f"origin {origin} scale {scale}: every crop matches again",
          got == wanted,
          f"{got} vs {wanted} -- if the crops do not come back the replay is "
          f"still comparing against different pixels")


# ===========================================================================
section("4. an entry with no layout falls back, and does not inherit")

# The subtle one. Without an explicit fallback, a frame with no layout is read
# under whatever the PREVIOUS frame in that worker happened to set -- so the
# same frame gives different answers depending on what ran before it, and the
# suite becomes order-dependent.
m.apply_layout(layout_at((500, 600), scale=1.9))
polluted = m.TRADE_REGION
restored = sc._restore_layout({})
check("no layout: reported as NOT replayable", restored is False,
      f"got {restored!r}")
check("no layout: geometry reset rather than inherited",
      m.TRADE_REGION != polluted,
      f"still {m.TRADE_REGION} -- a frame would be read under whatever ran "
      f"before it, making the suite order-dependent")
check("no layout: reset to the module default",
      m.TRADE_REGION == sc._DEFAULT_LAYOUT.trade,
      f"{m.TRADE_REGION} vs {sc._DEFAULT_LAYOUT.trade}")

m.apply_layout(layout_at((500, 600), scale=1.9))
restored = sc._restore_layout({"layout": {"origin": "nonsense"}})
check("malformed layout: treated as absent, not crashed", restored is False,
      f"got {restored!r}")
check("malformed layout: geometry still reset",
      m.TRADE_REGION == sc._DEFAULT_LAYOUT.trade, f"{m.TRADE_REGION}")


# ===========================================================================
section("5. coordinate comparison: exact when replayable, tolerant when not")

TOL = sc.LEGACY_COORD_TOLERANCE
cases = [
    ("identical",            (100, 200), "(100, 200)", True,  True,  True),
    ("1px off",              (101, 200), "(100, 200)", True,  False, True),
    ("5px off (the measured worst case)",
                             (105, 202), "(100, 200)", True,  False, True),
    (f"{TOL}px off, at the limit",
                             (100 + TOL, 200), "(100, 200)", True, False, True),
    (f"{TOL + 1}px off, past it",
                             (100 + TOL + 1, 200), "(100, 200)", True, False, False),
    ("wildly off",           (900, 900), "(100, 200)", True,  False, False),
]
for title, got, recorded, _x, exact_ok, legacy_ok in cases:
    check(f"replayable / {title}: {'accepted' if exact_ok else 'rejected'}",
          sc._coord_matches(got, recorded, True) is exact_ok,
          f"_coord_matches({got}, {recorded!r}, exact=True) = "
          f"{sc._coord_matches(got, recorded, True)}")
    check(f"legacy     / {title}: {'accepted' if legacy_ok else 'rejected'}",
          sc._coord_matches(got, recorded, False) is legacy_ok,
          f"_coord_matches({got}, {recorded!r}, exact=False) = "
          f"{sc._coord_matches(got, recorded, False)}")

check("tuples and the index's string form compare the same",
      sc._coord_matches((100, 200), (100, 200), True) is True, "")


# ===========================================================================
section("5b. 'found nothing' is a failure only when the layout is known")

# The tri-state. On a replayable frame the crop is reproduced exactly, so the
# reader finding nothing is a real regression. On a legacy frame it cannot be
# told apart from the crop having moved -- run_05284 is the worked example --
# so it is counted, never judged. Silently passing it would hide a genuine
# reader failure; failing it would report un-replayable history as a defect.
check("replayable + found nothing -> FAILURE",
      sc._coord_matches(None, "(100, 200)", True) is False,
      f"got {sc._coord_matches(None, '(100, 200)', True)!r} -- with the crop "
      f"reproduced, finding nothing is the reader regressing")
check("legacy + found nothing -> NOT DECIDABLE",
      sc._coord_matches(None, "(100, 200)", False) is None,
      f"got {sc._coord_matches(None, '(100, 200)', False)!r}")
check("not-decidable is distinguishable from a pass",
      sc._coord_matches(None, "(100, 200)", False) is not True, "")
check("not-decidable is distinguishable from a failure",
      sc._coord_matches(None, "(100, 200)", False) is not False, "")
check("an unparseable recorded value is also not decidable",
      sc._coord_matches((1, 2), "not a coordinate", False) is None,
      f"got {sc._coord_matches((1, 2), 'not a coordinate', False)!r}")
check("...but is a failure when the layout WAS reproduced",
      sc._coord_matches((1, 2), "not a coordinate", True) is False,
      f"got {sc._coord_matches((1, 2), 'not a coordinate', True)!r}")


# ===========================================================================
section("6. the tolerance is a concession, and stays small")

check(f"legacy tolerance is {TOL}px, comfortably under one sweep step",
      TOL <= 20,
      f"{TOL}px -- the NPC click offsets step in tens of pixels, so a "
      f"tolerance approaching that stops distinguishing a correct read from "
      f"a wrong one")
check("frames that CAN be replayed are still compared exactly",
      sc._coord_matches((101, 200), "(100, 200)", True) is False,
      "a recorded layout must not buy the tolerance as well")


m.apply_layout(sc._DEFAULT_LAYOUT)
print(f"\n{'-' * 70}")
print(f"{checks} checks, {len(failures)} FAILED")
for line in failures:
    print(f"  FAIL {line}")
raise SystemExit(1 if failures else 0)
