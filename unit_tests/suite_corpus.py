"""Run every reader against every frame the live script recorded.

The corpus is now exclusively frames the script itself captured while running
against the real game. That is a strictly better test set than passively
collected screenshots, for one reason: each frame arrives with the step that
produced it and the values the script believed at that moment. A passive
screenshot can only be checked against invariants; a recorded frame can be
checked against ground truth.

`table.target` is the sharpest of these. It records the row the script chose
along with that row's name, action, price and quantity, so re-reading the
frame must reproduce exactly what the running script read. If read_rows ever
drifts, this fails with the specific row it drifted on -- and that row is the
one that decides which listing gets cancelled and at what price.

Determinism is checked as well: a reader that gives two answers for identical
pixels cannot be trusted by anything downstream.

    py unit_tests\\suite_corpus.py            every frame, sampled determinism
    py unit_tests\\suite_corpus.py --full     every frame, determinism on all
    py unit_tests\\suite_corpus.py --limit 50 first 50 frames (quick check)
    py unit_tests\\suite_corpus.py --jobs 1   single process (for debugging)

SPEED

Every check here is OCR, and OCR is the whole cost: measured on this corpus a
frame takes ~4.4s across all readers, of which PNG decode is 0.04s and process
spawn 0.07s. The rest is Tesseract recognising regions that are deliberately
upscaled 2-3x before it sees them -- the upscale is what stopped 'Refresh'
splitting into 'R' + 'efresh', so it is not negotiable.

What IS negotiable is doing one frame at a time on a 16-core machine. Frames
are completely independent, so they run in a process pool. The default leaves
several cores free on purpose: the game and the live script are usually
running while this does, and starving them is worse than a slow test run.
"""

import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

import trade as m  # noqa: E402

CORPUS = Path(__file__).resolve().parent / "corpus"
INDEX = CORPUS / "run_index.jsonl"

# Determinism means re-running OCR, which doubles the cost of the slowest
# reader. Sampling keeps the default run tolerable; --full checks every frame.
# The sample is deterministic (every Nth), never random, so a failure found
# once can be reproduced.
DETERMINISM_EVERY = 4

# Worker processes. Fixed rather than scaled to the machine: these tests run
# while the game and the live recording script are up, and OCR is CPU-bound,
# so an unbounded pool competes directly with the thing being tested.
# Override for a one-off with --jobs N.
JOBS = 16

# How often the progress line is printed, in frames. Also printed on a 15s
# timer, so a slow stretch still reports instead of looking hung.
PROGRESS_EVERY = 25

# Steps whose frames should show the Trade window. Anything not listed here is
# read but not asserted on, so adding a record point cannot fail the suite
# until someone decides what it should prove.
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
    """Pool worker initialiser: let the parent own Ctrl+C.

    On Windows Ctrl+C is delivered to the whole console process group, so
    without this every worker raises KeyboardInterrupt at the same moment the
    parent does. The parent is then trying to join processes that are already
    dying, and the run hangs instead of stopping -- with workers left orphaned
    behind it.
    """
    import signal
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def check_frame(job):
    """Every assertion for one frame. Returns (ran, failures, label, truths).

    Pure with respect to the process it runs in -- it opens its own image and
    returns plain data -- so it can run in a worker pool unchanged.
    """
    entry, n, full = job
    name = entry["file"]
    label = entry.get("label", "?")
    ran = 0
    bad = []
    truths = 0

    def ok(cond, why):
        nonlocal ran
        ran += 1
        if not cond:
            bad.append(why)
        return bool(cond)

    path = CORPUS / name
    if not path.exists():
        return 0, [], label, 0
    shot = Image.open(path)

    trade = m.trade_window_open(shot)
    rows = m.read_rows(shot) if trade else []

    # -- determinism -------------------------------------------------------
    if full or n % DETERMINISM_EVERY == 0:
        ok([(r.index, r.name, r.price, r.qty, r.action) for r in rows]
           == [(r.index, r.name, r.price, r.qty, r.action)
               for r in (m.read_rows(shot) if trade else [])],
           f"{name} [{label}]: read_rows is not deterministic")
        ok(m.dialog_kind(shot) == m.dialog_kind(shot),
           f"{name} [{label}]: dialog_kind is not deterministic")
        ok(m.trade_window_open(shot) == trade,
           f"{name} [{label}]: trade_window_open is not deterministic")

    # -- the step says what should be on screen ----------------------------
    if label in TRADE_OPEN:
        ok(trade, f"{name} [{label}]: step implies the Trade window, none found")
    if label in REGISTER_TAB and trade:
        ok(m.register_tab_open(shot),
           f"{name} [{label}]: step implies the Register tab, not detected")

    # -- structural invariants on any readable table -----------------------
    floors = {0} | {f for *_, f in m.ITEM_PRICE_FLOORS}
    for r in rows:
        ok(1 <= r.index <= m.EXPECTED_ROWS, f"{name}: row index {r.index}")
        ok(r.action in ("change", "receive", "register"),
           f"{name}: odd action {r.action!r}")
        ok(r.price is None or r.price > 0, f"{name}: non-positive price")
        # 0 is a real quantity: the game prints it on a sold-out listing,
        # which then shows Complete/Receive. What cannot happen is a row
        # still on sale with no stock.
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

    # -- identity must round-trip on every live row ------------------------
    live = [r for r in rows if r.action in ("change", "receive")]
    for r in live:
        found, note = m.locate_row(rows, m.RowRef.of(r, rows))
        ok(found is not None and found.index == r.index,
           f"{name}: row {r.index} does not re-identify (note={note!r})")
    if live:
        _, note = m.locate_row(rows, m.RowRef("Nonexistent Item Zzz", 1, 1))
        ok(note == "missing", f"{name}: absent item read as {note!r}")

    # ---------------------------------------------------------------------
    # GROUND TRUTH: re-reading must reproduce what the running script saw.
    #
    # These are the assertions worth having. Everything above checks internal
    # consistency -- that a reading is well-formed. These check the reading
    # against what actually happened on a real screen at a real moment, which
    # is the only thing that catches a reader drifting.
    #
    # Every value here was recorded by the script itself as it acted on it, so
    # there is no hand-labelling and no opportunity for the label to be wrong
    # in a way the script would not also have been wrong about.
    # ---------------------------------------------------------------------
    def truth(cond, why):
        nonlocal truths
        truths += 1
        ok(cond, why)

    def row_matches(idx, source):
        """The recorded row must re-read identically, field for field."""
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

    # 1. the row the script chose to relist
    if label == "table.target" and entry.get("name"):
        truth(len(rows) == entry.get("visible", len(rows)),
              f"{name}: recorded {entry.get('visible')} rows, re-read {len(rows)}")
        row_matches(entry["row"], label)

    # 1b. and EVERY other row in that same table, where the snapshot exists.
    #     These cover the rows a relist never targets -- nothing else looks at
    #     them, so a reader that drifts only on row 9 would go unnoticed.
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

    # 2. the row the script was about to cancel -- same strength as above,
    #    and there are more of these than there are table.target frames
    if label == "cancel.before_change" and entry.get("name") and rows:
        row_matches(entry["row"], label)

    # 3. the NPC's exact position, not merely "she is somewhere"
    if label == "npc.found" and entry.get("centre"):
        got = m.find_npc(shot, retries=1)
        truth(str(got) == entry["centre"],
              f"{name}: NPC recorded at {entry['centre']}, re-read {got}")

    # 4. the inventory anchor every slot coordinate is derived from
    if label == "inventory.before_cancel" and entry.get("origin"):
        got = m.inventory_origin(shot)
        truth(str(got) == entry["origin"],
              f"{name}: origin recorded {entry['origin']}, re-read {got}")

    # 5. the market read -- the number the listing price comes from
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

    # 6. the register panel the quantity and price were taken from
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

    # 7. the inventory diff -- needs the before/after PAIR the script diffed,
    #    which main() attaches. This is the only check that exercises
    #    changed_slots on real pixels with a known answer.
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

    # -- what the script recorded about a cancel must still be true --------
    # Only BEFORE the change: `cancel.committed` is recorded after the listing
    # has been pulled, so the item is legitimately gone from the table by then
    # -- asserting presence there failed three frames that were correct. And
    # "gone" cannot be asserted either, because a duplicate stack of the same
    # item may still be listed.
    if label == "cancel.before_change" and entry.get("name") and rows:
        ok(bool(m.match_rows(rows, entry["name"])),
           f"{name} [{label}]: {entry['name']!r} not found in its own frame")

    # -- the inventory steps must show an inventory ------------------------
    if label.startswith("inventory."):
        origin = m.inventory_origin(shot)
        if ok(origin is not None, f"{name} [{label}]: no inventory anchor"):
            for rr, cc in ((1, 1), (1, m.GRID_SIZE),
                           (m.GRID_SIZE, 1), (m.GRID_SIZE, m.GRID_SIZE)):
                x, y = m.slot_centre_at(origin, rr, cc)
                ok(0 <= x < shot.width and 0 <= y < shot.height,
                   f"{name}: slot ({rr},{cc}) off screen at {x},{y}")

    return ran, bad, label, truths


def main():
    full = "--full" in sys.argv
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    # Fixed at 8, not derived from the core count. The game and the live
    # recording script normally run alongside this, so the budget has to be a
    # number someone chose with that in mind rather than whatever the machine
    # happens to have -- a cpu_count-derived default would quietly take 28
    # cores on a bigger box and starve the thing under test.
    jobs = JOBS
    if "--jobs" in sys.argv:
        jobs = max(1, int(sys.argv[sys.argv.index("--jobs") + 1]))

    # A fresh checkout has no corpus -- the script builds one by running. That
    # is the expected state on a new machine, not a failure, so it must not
    # make run_all.py report a red suite on a perfectly good install.
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

    # Attach the frames each inventory diff was computed from. The script
    # recorded the diff's ANSWER but the diff itself needs both inputs, so the
    # pair is reconstructed here from the order the steps were recorded in.
    # Done before --limit so a truncated run still pairs correctly.
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
    ran = truths = 0
    bad = []
    by_label = Counter()

    pool = None
    if jobs == 1:
        results = map(check_frame, jobs_list)
    else:
        from multiprocessing import Pool
        # Workers ignore Ctrl+C so the PARENT is the only one that handles it.
        # On Windows Ctrl+C is delivered to the whole console process group, so
        # without this all 16 workers raise KeyboardInterrupt at once while the
        # parent is blocked iterating results -- it then tries to join
        # processes that are already dying and the run hangs. Observed: a
        # 16-worker run that would not die, leaving orphaned workers behind.
        pool = Pool(processes=jobs, initializer=_ignore_sigint)
        results = pool.imap_unordered(check_frame, jobs_list, chunksize=4)

    total = len(jobs_list)
    done = frames_failed = 0
    last_print = started

    print(f"{'done':>12}  {'running':>7}  {'queued':>7}  {'cases':>9}  "
          f"{'passed':>9}  {'failed':>6}  {'rate':>9}  {'eta':>7}", flush=True)

    def consume():
        nonlocal ran, truths, done, frames_failed, last_print
        for r_ran, r_bad, r_label, r_truth in results:
            ran += r_ran
            bad.extend(r_bad)
            by_label[r_label] += 1
            truths += r_truth
            done += 1
            if r_bad:
                frames_failed += 1
                # Surface failures the moment they happen. Holding them to the
                # end means a ten-minute run tells you nothing for ten minutes,
                # and the first failure is usually the informative one.
                for why in r_bad:
                    print(f"  FAIL {why}", flush=True)

            now = time.monotonic()
            # Print on a frame interval OR a time interval, so a slow patch
            # still reports rather than looking hung.
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
        # terminate(), not close(): close() waits for every queued job to
        # finish, which on an interrupt is the rest of the corpus. This is also
        # why it lives in a finally -- it used to sit after the loop, so an
        # exception left the workers orphaned. Measured: four stragglers from a
        # killed run still resident half an hour later.
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
    print(f"  {'cases run':44} {ran:6,d}")
    print(f"  {'cases passed':44} {ran - len(bad):6,d}")
    print(f"  {'cases failed':44} {len(bad):6,d}")
    print(f"  {'workers':44} {jobs:6,d}")
    print(f"  {'elapsed':44} {elapsed/60:6.1f} min "
          f"({total/max(elapsed, 1e-9):.1f} frames/s)")
    if bad:
        # Grouped, because one broken reader produces the same failure on
        # hundreds of frames and an undifferentiated list buries the others.
        kinds = Counter(w.split(": ", 1)[-1].split("(")[0].strip() for w in bad)
        print(f"\n  distinct failure kinds: {len(kinds)}")
        for kind, n in kinds.most_common(15):
            print(f"    {n:5d}  {kind[:90]}")
        raise SystemExit(1)
    print("\nall good")


if __name__ == "__main__":
    main()
