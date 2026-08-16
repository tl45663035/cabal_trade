"""Measure candidate calibration anchors from recorded frames.

    py unit_tests\\find_anchors.py            # report candidates
    py unit_tests\\find_anchors.py -v         # include the rejected ones

READ-ONLY. It opens saved frames and runs OCR. It never touches the game and
never edits trade.py -- it prints a table for a human to copy from.

WHY THIS IS NOT A JUDGEMENT CALL. calibrate() fits an origin and a scale to the
anchors it finds. A wrong reference position does not fail loudly: it is
absorbed into `scale`, so calibration reports success and every derived
coordinate is quietly wrong by a proportion of the window. The file already
records two anchors that had to be removed for exactly that -- "Function" was
out by 74px in y, and "Purchase" was never found at 1440p because the game
draws its FPS overlay across that tab.

So a candidate has to earn its place on evidence, and this measures the five
things that matter:

  PRESENT     found in every frame examined, not most of them
  UNIQUE      exactly one hit per frame -- two hits and the fit picks
              arbitrarily between them, which moves the origin frame to frame
  STABLE      its position varies by at most a pixel or two across frames once
              each frame's own layout is removed
  CONFIDENT   OCRs well above the bar, because a marginal word becomes a
              marginal anchor
  CHROME      present on the Purchase tab as well as the Register tab, or
              calibrating on the wrong tab finds nothing

Positions are reported in REFERENCE coordinates -- (measured - origin) / scale,
using the layout recorded beside each frame -- so they can be pasted straight
into REF_ANCHORS.
"""
import collections
import json
import statistics
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import trade as m  # noqa: E402

m.NO_INPUT = True

CORPUS = _ROOT / "unit_tests" / "corpus"
INDEX = CORPUS / "run_index.jsonl"
VERBOSE = "-v" in sys.argv

# How many frames of each tab to measure. More is better evidence and costs
# OCR time; twelve of each was enough to separate the stable words from the
# rest by an order of magnitude.
SAMPLE = 12
# A candidate must sit within this many reference pixels of its own median in
# every frame. The existing anchors fit to a worst residual of 1.7px, so
# anything looser is not evidence of a fixed position.
MAX_DRIFT = 2.5
# ...and read at least this well. NEAR_ANCHOR_MIN_CONF is 70 for a clipped
# word; a whole-word anchor should be far better than that.
MIN_CONF = 85.0


def frames_by_tab():
    """Recorded frames grouped by which tab they show, newest first."""
    if not INDEX.exists():
        return {}
    rows = []
    for line in INDEX.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not (row.get("layout") or {}).get("origin"):
            continue
        if (CORPUS / (row.get("file") or "")).exists():
            rows.append(row)
    rows.reverse()

    from PIL import Image
    out = {"register": [], "purchase": []}
    for row in rows:
        if len(out["register"]) >= SAMPLE and len(out["purchase"]) >= SAMPLE:
            break
        path = CORPUS / row["file"]
        try:
            shot = Image.open(path)
        except Exception:  # noqa: BLE001
            continue
        if not m.trade_window_open(shot):
            continue
        if m.purchase_tab_open(shot) and len(out["purchase"]) < SAMPLE:
            out["purchase"].append((path, shot, row["layout"]))
        elif m.register_tab_open(shot) and len(out["register"]) < SAMPLE:
            out["register"].append((path, shot, row["layout"]))
    return out


def to_reference(point, layout):
    """A measured point, expressed in reference coordinates."""
    ox, oy = layout["origin"]
    scale = layout.get("scale") or 1.0
    return ((point[0] - ox) / scale, (point[1] - oy) / scale)


def measure(samples):
    """{word: [(ref_x, ref_y, conf, hits_in_frame), ...]} over the samples."""
    seen = collections.defaultdict(list)
    for _path, shot, layout in samples:
        words = m.find_words(shot, m.TRADE_REGION, 20)
        by_text = collections.defaultdict(list)
        for w in words:
            text = w.text.strip()
            # Words only: a number moves with the data, and punctuation is
            # never a reliable single hit.
            if len(text) < 4 or not text.isalpha():
                continue
            by_text[text].append(w)
        for text, hits in by_text.items():
            best = max(hits, key=lambda w: w.conf)
            rx, ry = to_reference(best.centre, layout)
            seen[text].append((rx, ry, best.conf, len(hits)))
    return seen


def main() -> int:
    tabs = frames_by_tab()
    reg, pur = tabs.get("register", []), tabs.get("purchase", [])
    print(f"measuring {len(reg)} Register-tab and {len(pur)} Purchase-tab "
          f"frames\n")
    if not reg:
        print("No usable frames. The corpus is gitignored session data, so "
              "this only runs on a machine that has recorded some.")
        return 2

    on_reg, on_pur = measure(reg), measure(pur)
    current = {name for name, _ in m.REF_ANCHORS}

    rows = []
    for word, obs in on_reg.items():
        if len(obs) < len(reg):
            if VERBOSE:
                rows.append((word, None, f"only in {len(obs)}/{len(reg)} frames"))
            continue
        if any(hits != 1 for *_r, hits in obs):
            if VERBOSE:
                rows.append((word, None, "not unique on screen"))
            continue
        xs = [o[0] for o in obs]
        ys = [o[1] for o in obs]
        conf = statistics.mean(o[2] for o in obs)
        mx, my = statistics.median(xs), statistics.median(ys)
        drift = max(max(abs(x - mx) for x in xs), max(abs(y - my) for y in ys))
        if drift > MAX_DRIFT:
            if VERBOSE:
                rows.append((word, None, f"drifts {drift:.1f}px"))
            continue
        if conf < MIN_CONF:
            if VERBOSE:
                rows.append((word, None, f"confidence {conf:.0f}"))
            continue
        chrome = word in on_pur and len(on_pur[word]) == len(pur) and pur
        rows.append((word, (round(mx), round(my), conf, drift, bool(chrome)),
                     ""))

    good = [r for r in rows if r[1]]
    good.sort(key=lambda r: (not r[1][4], -r[1][2]))

    print(f"{'word':16} {'reference':>14} {'conf':>6} {'drift':>7}  "
          f"{'both tabs':>9}  status")
    print("-" * 74)
    for word, stats, _why in good:
        mx, my, conf, drift, chrome = stats
        status = "ALREADY AN ANCHOR" if word in current else (
            "candidate" if chrome else "Register tab only - NOT usable")
        print(f"{word:16} {f'({mx}, {my})':>14} {conf:>6.1f} {drift:>6.1f}px  "
              f"{str(chrome):>9}  {status}")

    if VERBOSE:
        print("\nrejected:")
        for word, stats, why in rows:
            if not stats:
                print(f"  {word:16} {why}")

    print("\n--- the anchors in use today ---")
    for name, ref in m.REF_ANCHORS:
        found = next((r for r in good if r[0] == name), None)
        if found is None:
            print(f"  {name:16} {str(ref):>14}  NOT MEASURABLE in these frames")
            continue
        mx, my = found[1][0], found[1][1]
        off = max(abs(mx - ref[0]), abs(my - ref[1]))
        flag = "" if off <= MAX_DRIFT else f"   <-- OFF BY {off:.0f}px"
        print(f"  {name:16} {str(ref):>14} measured ({mx}, {my}){flag}")

    usable = [r for r in good if r[1][4] and r[0] not in current]
    print(f"\n{len(usable)} new candidate(s) that appear on BOTH tabs.")
    if usable:
        print("\nPaste-ready:\n")
        for word, stats, _ in usable:
            print(f'    ("{word}", ({stats[0]}, {stats[1]})),'
                  f'   # conf {stats[2]:.0f}, drift {stats[3]:.1f}px')
    print("\nAdding one still needs judgement: an anchor must be chrome that "
          "survives every state the window is in, and these frames only prove "
          "the states that were recorded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
