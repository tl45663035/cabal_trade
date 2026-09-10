
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image

import trade as m

CORPUS = Path(__file__).resolve().parent / "corpus"
INDEX = CORPUS / "run_index.jsonl"

DETERMINISM_EVERY = 4

JOBS = 16

PROGRESS_EVERY = 25

TRADE_OPEN = {
    "shop.opened", "tab.before_register_click", "tab.register_open",
    "refresh.before", "refresh.after", "table.target", "sanity.start",
    "cancel.before_change", "cancel.committed",
    "register.before_load", "register.priced", "register.committed",
    "price.suggestions", "price.before_select", "qty.before_typing",
}
REGISTER_TAB = {
    "tab.register_open", "refresh.before", "refresh.after", "table.target",
    "cancel.before_change", "sanity.start",
}


def _ignore_sigint():
    import signal
    signal.signal(signal.SIGINT, signal.SIG_IGN)


_DEFAULT_LAYOUT = m.LAYOUT

LEGACY_COORD_TOLERANCE = 12


def _restore_layout(entry) -> bool:
    spec = entry.get("layout")
    if not spec:
        m.apply_layout(_DEFAULT_LAYOUT)
        return False
    try:
        m.apply_layout(m.Layout(screen=tuple(spec["screen"]),
                                origin=tuple(spec["origin"]),
                                scale=float(spec["scale"]),
                                measured_from="recorded"))
        return True
    except Exception:
        m.apply_layout(_DEFAULT_LAYOUT)
        return False


def _as_point(value):
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return int(value[0]), int(value[1])
    digits = re.findall(r"-?\d+", str(value))
    return (int(digits[0]), int(digits[1])) if len(digits) >= 2 else None


def _coord_matches(got, recorded, exact: bool):
    if str(got) == str(recorded):
        return True
    if exact:
        return False
    if got is None:
        return None
    a, b = _as_point(got), _as_point(recorded)
    if a is None or b is None:
        return None
    return (abs(a[0] - b[0]) <= LEGACY_COORD_TOLERANCE
            and abs(a[1] - b[1]) <= LEGACY_COORD_TOLERANCE)


def check_frame(job):
    entry, n, full = job
    name = entry["file"]
    label = entry.get("label", "?")
    ran = 0
    bad = []
    truths = 0
    undecidable = 0

    def ok(cond, why):
        nonlocal ran
        ran += 1
        if not cond:
            bad.append(why)
        return bool(cond)

    path = CORPUS / name
    if not path.exists():
        return 0, [], label, 0, 0
    shot = Image.open(path)

    replayed = _restore_layout(entry)

    trade = m.trade_window_open(shot)
    rows = m.read_rows(shot) if trade else []

    if full or n % DETERMINISM_EVERY == 0:
        ok([(r.index, r.name, r.price, r.qty, r.action) for r in rows]
           == [(r.index, r.name, r.price, r.qty, r.action)
               for r in (m.read_rows(shot) if trade else [])],
           f"{name} [{label}]: read_rows is not deterministic")
        ok(m.dialog_kind(shot) == m.dialog_kind(shot),
           f"{name} [{label}]: dialog_kind is not deterministic")
        ok(m.trade_window_open(shot) == trade,
           f"{name} [{label}]: trade_window_open is not deterministic")

    if label in TRADE_OPEN:
        ok(trade, f"{name} [{label}]: step implies the Trade window, none found")
    if label in REGISTER_TAB and trade:
        ok(m.register_tab_open(shot),
           f"{name} [{label}]: step implies the Register tab, not detected")

    floors = {0} | {f for *_, f in m.ITEM_PRICE_FLOORS}
    for r in rows:
        ok(1 <= r.index <= m.EXPECTED_ROWS, f"{name}: row index {r.index}")
        ok(r.action in ("change", "receive", "register"),
           f"{name}: odd action {r.action!r}")
        ok(r.price is None or r.price > 0, f"{name}: non-positive price")
        ok(r.qty is None or r.qty >= 0, f"{name}: negative qty {r.qty}")
        ok(r.qty != 0 or r.action == "receive",
           f"{name}: row {r.index} qty 0 but action {r.action!r}")
        ok(r.bottom > r.top, f"{name}: inverted row band")
        if r.action == "register":
            ok(r.name == "(empty)", f"{name}: empty row named {r.name!r}")
        ok(m.item_price_floor(r.name) in floors,
           f"{name}: odd floor {m.item_price_floor(r.name):,} for {r.name!r}")
    if len(rows) > 1:
        gaps = [b.top - a.top for a, b in zip(rows, rows[1:])]
        ok(max(gaps) - min(gaps) <= 4, f"{name}: uneven row pitch {gaps}")

    live = [r for r in rows if r.action in ("change", "receive")]
    for r in live:
        found, note = m.locate_row(rows, m.RowRef.of(r, rows))
        ok(found is not None and found.index == r.index,
           f"{name}: row {r.index} does not re-identify (note={note!r})")
    if live:
        _, note = m.locate_row(rows, m.RowRef("Nonexistent Item Zzz", 1, 1))
        ok(note == "missing", f"{name}: absent item read as {note!r}")

    def truth(cond, why):
        nonlocal truths
        truths += 1
        ok(cond, why)

    def row_matches(idx, source):
        actual = next((r for r in rows if r.index == idx), None)
        if not ok(actual is not None,
                  f"{name} [{source}]: recorded row {idx} is not there now"):
            return
        for field in ("name", "action", "price", "qty"):
            if entry.get(field) is None:
                continue
            truth(entry[field] == getattr(actual, field),
                  f"{name} [{source}] row {idx} {field}: "
                  f"recorded {entry[field]!r}, re-read {getattr(actual, field)!r}")

    if label == "table.target" and entry.get("name"):
        truth(len(rows) == entry.get("visible", len(rows)),
              f"{name}: recorded {entry.get('visible')} rows, re-read {len(rows)}")
        row_matches(entry["row"], label)

    for snap in entry.get("table") or []:
        idx, s_name, s_action, s_price, s_qty = snap
        actual = next((r for r in rows if r.index == idx), None)
        if not ok(actual is not None,
                  f"{name} [table snapshot]: row {idx} is not there now"):
            continue
        for field, want in (("name", s_name), ("action", s_action),
                            ("price", s_price), ("qty", s_qty)):
            truth(want == getattr(actual, field),
                  f"{name} [table snapshot] row {idx} {field}: "
                  f"recorded {want!r}, re-read {getattr(actual, field)!r}")

    if label == "cancel.before_change" and entry.get("name") and rows:
        row_matches(entry["row"], label)

    def coord_truth(got, recorded, what):
        nonlocal undecidable
        verdict = _coord_matches(got, recorded, replayed)
        if verdict is None:
            undecidable += 1
            return
        truth(verdict,
              f"{name}: {what} recorded {recorded}, re-read {got}"
              + ("" if replayed else "  (no recorded layout; compared within "
                                     f"{LEGACY_COORD_TOLERANCE}px)"))

    if label == "npc.found" and entry.get("centre"):
        coord_truth(m.find_npc(shot, retries=1), entry["centre"], "NPC")

    if label == "inventory.before_cancel" and entry.get("origin"):
        coord_truth(m.inventory_origin(shot), entry["origin"], "origin")

    if label in ("price.suggestions", "price.before_select"):
        panel = m.read_register_panel(shot)
        seen = panel.get("price_rows")
        if entry.get("rows"):
            truth(str(seen) == entry["rows"],
                  f"{name}: price rows recorded {entry['rows']}, re-read {seen!r}")
        if entry.get("lowest") and seen:
            low = min(seen, key=lambda r: abs(r[1] - m.PRICE_BOTTOM_Y))
            truth(low[0] == entry["lowest"],
                  f"{name}: lowest recorded {entry['lowest']:,}, re-read {low[0]:,}")
        if entry.get("y") and seen:
            truth(any(abs(y - entry["y"]) <= 2 for _, y in seen),
                  f"{name}: clicked row at y={entry['y']}, "
                  f"re-read rows at {[y for _, y in seen]}")

    if label in ("register.priced", "register.committed"):
        panel = m.read_register_panel(shot)
        ok(isinstance(panel, dict), f"{name}: register panel unreadable")
        if entry.get("net_sales") and panel.get("net_sales"):
            truth(panel["net_sales"] == entry["net_sales"],
                  f"{name}: net sales recorded {entry['net_sales']:,}, "
                  f"re-read {panel['net_sales']:,}")
        if entry.get("qty") and panel.get("qty"):
            truth(panel["qty"] == entry["qty"],
                  f"{name}: panel qty recorded {entry['qty']}, "
                  f"re-read {panel['qty']}")

    if label == "inventory.returned" and entry.get("_before") and entry.get("_after"):
        before = CORPUS / entry["_before"]
        after = CORPUS / entry["_after"]
        if before.exists() and after.exists():
            b_img = Image.open(before)
            origin = m.inventory_origin(b_img)
            if origin is not None:
                got = m.changed_slots(b_img, Image.open(after), origin)
                truth(len(got) == entry.get("count"),
                      f"{name}: diff recorded {entry.get('count')} slots, "
                      f"re-read {len(got)}")
                truth(", ".join(f"{r},{c}" for r, c in got) == entry.get("slots"),
                      f"{name}: slots recorded {entry.get('slots')!r}, "
                      f"re-read {', '.join(f'{r},{c}' for r, c in got)!r}")

    if label == "cancel.before_change" and entry.get("name") and rows:
        ok(bool(m.match_rows(rows, entry["name"])),
           f"{name} [{label}]: {entry['name']!r} not found in its own frame")

    if label.startswith("inventory."):
        origin = m.inventory_origin(shot)
        if ok(origin is not None, f"{name} [{label}]: no inventory anchor"):
            for rr, cc in ((1, 1), (1, m.GRID_SIZE),
                           (m.GRID_SIZE, 1), (m.GRID_SIZE, m.GRID_SIZE)):
                x, y = m.slot_centre_at(origin, rr, cc)
                ok(0 <= x < shot.width and 0 <= y < shot.height,
                   f"{name}: slot ({rr},{cc}) off screen at {x},{y}")

    return ran, bad, label, truths, undecidable


def main():
    full = "--full" in sys.argv
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    jobs = JOBS
    if "--jobs" in sys.argv:
        jobs = max(1, int(sys.argv[sys.argv.index("--jobs") + 1]))

    if not INDEX.exists():
        print("no recorded frames on this machine yet - nothing to check.")
        print("Run the script once; it records a frame at every step, and this")
        print("suite then has something to run against.")
        return

    entries = []
    for line in INDEX.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                entries.append(json.loads(line))
            except ValueError:
                pass
    entries = [e for e in entries if (CORPUS / e.get("file", "")).exists()]

    pending_before = pending_after = None
    for e in entries:
        lab = e.get("label")
        if lab == "inventory.before_cancel":
            pending_before, pending_after = e.get("file"), None
        elif lab == "inventory.after_cancel":
            pending_after = e.get("file")
        elif lab == "inventory.returned" and pending_before and pending_after:
            e["_before"], e["_after"] = pending_before, pending_after

    if limit:
        entries = entries[:limit]

    labels = Counter(e.get("label") for e in entries)
    print(f"recorded frames: {len(entries)}   distinct steps: {len(labels)}")
    print(f"determinism: {'every frame' if full else f'every {DETERMINISM_EVERY}th frame'}")
    print(f"workers: {jobs} of {os.cpu_count()} cores\n")

    jobs_list = [(e, n, full) for n, e in enumerate(entries)]
    started = time.monotonic()
    ran = truths = skipped = 0
    bad = []
    by_label = Counter()

    pool = None
    if jobs == 1:
        results = map(check_frame, jobs_list)
    else:
        from multiprocessing import Pool
        pool = Pool(processes=jobs, initializer=_ignore_sigint)
        results = pool.imap_unordered(check_frame, jobs_list, chunksize=4)

    total = len(jobs_list)
    done = frames_failed = 0
    last_print = started

    print(f"{'done':>12}  {'running':>7}  {'queued':>7}  {'cases':>9}  "
          f"{'passed':>9}  {'failed':>6}  {'rate':>9}  {'eta':>7}", flush=True)

    def consume():
        nonlocal ran, truths, skipped, done, frames_failed, last_print
        for r_ran, r_bad, r_label, r_truth, r_skip in results:
            ran += r_ran
            bad.extend(r_bad)
            by_label[r_label] += 1
            truths += r_truth
            skipped += r_skip
            done += 1
            if r_bad:
                frames_failed += 1
                for why in r_bad:
                    print(f"  FAIL {why}", flush=True)

            now = time.monotonic()
            if done % PROGRESS_EVERY == 0 or now - last_print >= 15 or done == total:
                last_print = now
                rate = done / max(now - started, 1e-9)
                eta = (total - done) / max(rate, 1e-9)
                running = min(jobs, total - done)
                print(f"  {done:>5}/{total:<5} {running:>7}  {total-done:>7}  "
                      f"{ran:>9,}  {ran - len(bad):>9,}  {len(bad):>6,}  "
                      f"{rate:>6.1f}/s  {eta/60:>5.1f}m", flush=True)

    interrupted = False
    try:
        consume()
    except KeyboardInterrupt:
        interrupted = True
        print(f"\ninterrupted after {done}/{total} frames "
              f"({ran:,} cases, {len(bad)} failures so far)", flush=True)
    finally:
        if pool is not None:
            pool.terminate()
            pool.join()
    if interrupted:
        raise SystemExit(130)

    elapsed = time.monotonic() - started
    print(f"\n{'step':32} {'frames':>7}")
    for label, count in sorted(by_label.items(), key=lambda kv: -kv[1]):
        print(f"  {label:30} {count:7d}")

    print(f"\n  {'frames checked':44} {done:6,d}")
    print(f"  {'frames with a failure':44} {frames_failed:6,d}")
    print(f"  {'frames clean':44} {done - frames_failed:6,d}")
    print(f"  {'GROUND-TRUTH assertions':44} {truths:6,d}")
    print(f"  {'coordinate checks skipped (no layout)':44} {skipped:6,d}")
    print(f"  {'cases run':44} {ran:6,d}")
    print(f"  {'cases passed':44} {ran - len(bad):6,d}")
    print(f"  {'cases failed':44} {len(bad):6,d}")
    print(f"  {'workers':44} {jobs:6,d}")
    print(f"  {'elapsed':44} {elapsed/60:6.1f} min "
          f"({total/max(elapsed, 1e-9):.1f} frames/s)")
    if bad:
        kinds = Counter(w.split(":", 1)[-1].strip()[:70] for w in bad)
        print(f"\n  distinct failure kinds: {len(kinds)}")
        for kind, n in kinds.most_common(15):
            print(f"    {n:5d}  {kind[:90]}")
        raise SystemExit(1)
    print("\nall good")


if __name__ == "__main__":
    main()

