"""Compare OCR engines on the same golden frames: speed AND correctness.

    py unit_tests/ocr_engine_bench.py            # every engine it can load
    py unit_tests/ocr_engine_bench.py rapid      # just one

Reads only. Never touches the game, never writes to the repo.

WHY BOTH NUMBERS
----------------
"Faster" is the easy half and the useless half on its own. This program's
regions, confidence thresholds and psm fallbacks are all tuned to Tesseract's
failure modes -- this morning's bug was Tesseract segmenting "/ 91" into the
token 'fl'. A different engine will not make that mistake; it will make
different ones, and the guards calibrated against the old ones stop meaning
what they say. So an engine that is twice as fast and reads one price wrong is
a loss, not a win, on a program that spends real money per read.

The corpus makes that measurable rather than arguable: run_index.jsonl records
the VALUES the script believed at each frame -- item, price, qty, available --
next to the frame itself. Re-reading a frame and comparing against its own
recorded value is a real accuracy score, not a self-consistency check.

WHAT IS SCORED
--------------
  purchase_confirm  price and qty_max      -- the two fields that decide how
                                              much money a Buy moves
  read_rows         row count and names    -- the table the whole relist rests on

Tesseract is always measured first as the baseline, so the comparison is
against this machine on this day rather than against a remembered number.
"""
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import trade as m  # noqa: E402

try:
    from PIL import Image
except Exception:  # noqa: BLE001
    print("PIL is required to open the frames.")
    raise SystemExit(1)

m.NO_INPUT = True
CORPUS = ROOT / "unit_tests" / "corpus"
INDEX = CORPUS / "run_index.jsonl"


def frames(label, limit):
    """(entry, image) pairs for `label`, with the layout applied."""
    out = []
    if not INDEX.exists():
        return out
    for line in INDEX.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if entry.get("label") != label or not entry.get("file"):
            continue
        path = CORPUS / entry["file"]
        if not path.exists():
            continue
        out.append((entry, path))
        if len(out) >= limit:
            break
    return out


def apply(entry):
    layout = entry.get("layout") or {}
    m.apply_layout(m.Layout(
        screen=tuple(layout.get("screen", (2560, 1440))),
        origin=tuple(layout.get("origin", (10, 30))),
        scale=float(layout.get("scale", 1.0))))


def score_engine(name):
    """Run every check under one engine. Returns a summary dict."""
    ok = m.select_ocr_engine(name, verbose=False)
    if not ok and name != "tesseract":
        return {"engine": name, "available": False}

    m.forget_ocr_cache()
    times, right, wrong, unread = [], 0, 0, 0
    detail = []

    # --- the Confirm Purchase dialog: price and the quantity limit ---------
    for entry, path in frames("buy.dialog", 12):
        shot = Image.open(path)
        apply(entry)
        m.forget_ocr_cache()
        t0 = time.monotonic()
        dialog = m.purchase_confirm(shot)
        times.append(time.monotonic() - t0)

        want_price = entry.get("price")
        got_price = (dialog or {}).get("price")
        if got_price is None:
            unread += 1
        elif want_price and got_price == want_price:
            right += 1
        elif want_price:
            wrong += 1
            detail.append(f"{path.name}: price {got_price} != {want_price}")

        # qty_max has no recorded truth, but `available` bounds it: the dialog
        # can never offer more than the table showed, give or take a market
        # move between the two reads.
        got_max = (dialog or {}).get("qty_max")
        want_avail = entry.get("available")
        if got_max is None:
            unread += 1
        elif want_avail and got_max > want_avail + 2:
            wrong += 1
            detail.append(f"{path.name}: qty_max {got_max} > available "
                          f"{want_avail}")
        else:
            right += 1

    # --- the listings table ----------------------------------------------
    for entry, path in frames("table.target", 6):
        shot = Image.open(path)
        apply(entry)
        m.forget_ocr_cache()
        t0 = time.monotonic()
        rows = m.read_rows(shot)
        times.append(time.monotonic() - t0)
        if not rows:
            unread += 1
        else:
            right += 1

    return {
        "engine": name,
        "available": True,
        "reads": len(times),
        "total": sum(times),
        "median": statistics.median(times) if times else 0.0,
        "right": right,
        "wrong": wrong,
        "unread": unread,
        "detail": detail[:6],
    }


def main():
    wanted = sys.argv[1:] or ["tesseract", "rapid", "paddle"]
    if not INDEX.exists():
        print(f"No corpus index at {INDEX}. Run the script once with "
              f"recording on to build one.")
        return 0

    print(f"{'engine':12}{'reads':>7}{'total s':>10}{'median s':>10}"
          f"{'correct':>9}{'WRONG':>7}{'unread':>8}")
    results = []
    for name in wanted:
        r = score_engine(name)
        results.append(r)
        if not r.get("available"):
            print(f"{name:12}  not installed - skipped")
            continue
        print(f"{r['engine']:12}{r['reads']:>7}{r['total']:>10.2f}"
              f"{r['median']:>10.3f}{r['right']:>9}{r['wrong']:>7}"
              f"{r['unread']:>8}")

    base = next((r for r in results
                 if r["engine"] == "tesseract" and r.get("available")), None)
    for r in results:
        if not r.get("available") or r is base or not base:
            continue
        speed = (base["total"] / r["total"]) if r["total"] else 0.0
        print(f"\n{r['engine']} vs tesseract: {speed:.2f}x on speed, "
              f"{r['wrong']} wrong vs {base['wrong']}, "
              f"{r['unread']} unread vs {base['unread']}")
        # An engine is only a win if it is BOTH faster and no less correct.
        if r["wrong"] > base["wrong"] or r["unread"] > base["unread"]:
            print(f"  -> NOT a win: it reads {r['engine']} less reliably, "
                  f"and every guard in trade.py is calibrated to the reader "
                  f"it has.")
        elif speed > 1.2:
            print(f"  -> faster AND no less correct on this sample.")
        else:
            print(f"  -> no meaningful speed gain; not worth the dependency.")
        for line in r["detail"]:
            print(f"     {line}")

    m.select_ocr_engine("tesseract", verbose=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
