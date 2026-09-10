import collections
import json
import pathlib
import sys

sys.path.insert(0, r"C:\Users\Trung\Cabal")

HERE = pathlib.Path(__file__).resolve().parent
INDEX = HERE / "corpus" / "run_index.jsonl"

failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


if not INDEX.exists():
    print(f"SKIPPED: no corpus index at {INDEX}")
    raise SystemExit(0)

rows = [json.loads(l) for l in
        INDEX.read_text(encoding="utf-8").splitlines() if l.strip()]
rows.sort(key=lambda r: (r.get("at", ""), r.get("file", "")))

episodes, cur = [], []
for r in rows:
    if r["label"] == "table.target" and cur:
        episodes.append(cur)
        cur = []
    cur.append(r)
if cur:
    episodes.append(cur)

print(f"{len(rows)} frames -> {len(episodes)} episode(s)")


def labels(ep):
    return [f["label"] for f in ep]


def first(seq, label):
    return seq.index(label) if label in seq else None



RUN_START_LABELS = {"warlag.clock_synced", "cycle.start", "npc.found",
                    "shop.opened"}
GAP_SECONDS = 120.0


def truncated(ep) -> bool:
    import datetime as _dt
    for a, b in zip(ep, ep[1:]):
        gap = (_dt.datetime.fromisoformat(b["at"])
               - _dt.datetime.fromisoformat(a["at"])).total_seconds()
        if gap > GAP_SECONDS and b["label"] in RUN_START_LABELS:
            return True
    return False


truncated_n = 0
for n, ep in enumerate(episodes):
    seq = labels(ep)
    where = f"episode {n} ({ep[0].get('file', '?')})"

    if n == 0:
        continue
    if truncated(ep):
        truncated_n += 1
        continue

    c = first(seq, "cancel.committed")
    ret = first(seq, "inventory.returned")
    reg = first(seq, "register.before_load")
    if c is not None and reg is not None:
        check(ret is not None and c < ret < reg,
              f"{where}: cancel.committed -> register.before_load with no "
              f"inventory.returned between them ({' -> '.join(seq)})")

    ab = first(seq, "cancel.aborted")
    com = first(seq, "register.committed")
    if ab is not None:
        check(com is None or com < ab,
              f"{where}: register.committed AFTER cancel.aborted -- a listing "
              f"was made off a cancel that did not complete "
              f"({' -> '.join(seq)})")

    if com is not None:
        sug = first(seq, "price.suggestions")
        check(sug is not None and sug < com,
              f"{where}: register.committed with no price.suggestions before "
              f"it -- the price was not read from the panel "
              f"({' -> '.join(seq)})")
        qty = first(seq, "qty.before_typing")
        check(qty is not None and qty < com,
              f"{where}: register.committed with no qty.before_typing before "
              f"it -- the quantity was never set ({' -> '.join(seq)})")

    if c is not None:
        ended_ok = (com is not None
                    or "worktab.carrying" in seq
                    or "relist.stranded" in seq)
        check(ended_ok,
              f"{where}: the cancel COMMITTED and the episode ends without a "
              f"registration or a strand marker -- an item left the shop and "
              f"nothing records where it went ({' -> '.join(seq)})")

    if c is not None:
        bc = first(seq, "cancel.before_change")
        check(bc is not None and bc < c,
              f"{where}: cancel.committed with no cancel.before_change before "
              f"it ({' -> '.join(seq)})")

    check(seq[0] in ("table.target",) or n == 0,
          f"{where}: episode does not begin at table.target (begins {seq[0]!r})")


pairs = collections.Counter()
for ep in episodes:
    seq = labels(ep)
    for a, b in zip(seq, seq[1:]):
        pairs[(a, b)] += 1

check(("cancel.committed", "register.committed") not in pairs,
      "cancel.committed is immediately followed by register.committed "
      "somewhere -- nothing located the item in between")

check(("cancel.aborted", "register.committed") not in pairs,
      "cancel.aborted is immediately followed by register.committed somewhere")

shapes = {tuple(labels(e)) for e in episodes}
has_abort = any("cancel.aborted" in s for s in shapes)
has_sold = any("sale.collected" in s for s in shapes)
has_carry = any("worktab.carrying" in s for s in shapes)
check(has_abort, "the corpus must contain at least one ABORTED cancel, or the "
                 "recovery rules above are never exercised")
check(has_sold, "and at least one collected sale")
check(has_carry, "and at least one carried work tab")

print(f"  {len(shapes)} distinct episode shape(s); "
      f"abort={has_abort} sold={has_sold} carry={has_carry}")
print(f"  {truncated_n} episode(s) truncated by a killed process, not asserted "
      f"on -- see the note above: a force-kill loses the record, not the work")

check(truncated_n < len(episodes) // 2,
      f"{truncated_n} of {len(episodes)} episodes are truncated -- too much of "
      f"the corpus is the tail of a killed run to draw conclusions from")

print(f"sequence_test: {checks} checks, {len(failures)} failure(s)")
for line in failures[:20]:
    print("  FAIL", line)
if len(failures) > 20:
    print(f"  ... and {len(failures) - 20} more")
sys.exit(1 if failures else 0)
