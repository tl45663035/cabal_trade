"""The corpus as SEQUENCES: what must follow what, and what must never.

Every other suite here asks "is this frame self-consistent with its own
label". None of them asks "is this frame a legal successor to the last one" --
suite_corpus deliberately cannot, because it feeds frames through a 16-worker
pool in whatever order they arrive, and its own comments warn against relying
on "whatever the previous frame in this worker happened to set".

That leaves a whole class of defect untested, and it is the class that has
actually cost this project runs:

  * the Item Information tooltip covering the registration dialog -- 63 aborts
    before anyone root-caused it. Every individual frame was VALID; what was
    wrong was a window being present at a step where it has no business being.
  * the craft window failing because leave_shop() had closed the Inventory
    panel. Again every frame valid on its own; the fault was purely "what is
    on screen after this action".

Parallelism is kept by splitting on EPISODES rather than frames: each episode
is one row's cancel-and-relist, ordered within itself and independent of every
other, so a worker can own one entire episode and ordering survives.

THE RULES BELOW ARE WRITTEN FROM THE CODE'S INTENT, NOT READ OFF THE CORPUS.
That distinction is the whole value. Deriving "legal" transitions from the
recordings and then asserting the recordings comply is circular, and this
project has been burned by exactly that -- an audit found 91.8% of an earlier
generation of assertions bit-identically circular and none independent. Where
these hand-written rules and the recordings disagree, that is a finding: either
the code did something it should not, or the rule is wrong. Both are worth
knowing, and neither is discoverable from a suite that agrees with itself.
"""
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

# An EPISODE is one row's journey. table.target is where relist() commits to a
# particular listing, so it is the natural cut.
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


# ==========================================================================
# THE RULES. Each is a property of the flow, stated independently of what the
# corpus happens to contain.
# ==========================================================================

# TRUNCATED EPISODES ARE NOT STRANDS.
#
# A force-killed process drops the buffered tail of both the log and this
# index, so an episode can end mid-action in the record while the action
# itself completed in the game. Measured on 2026-08-09: a run killed during
# "Setting quantity" left an episode ending at qty.before_typing with no
# registration -- and the NEXT run found that row listed at 194,127,300, which
# is precisely the price the killed run had just read as the market's lowest.
# The registration happened; only the record of it was lost.
#
# The tell is a long silence followed by a new run's opening labels. Treating
# that as a strand reports the way the script was STOPPED as a defect in the
# script, so it is separated out and counted rather than asserted on.
RUN_START_LABELS = {"warlag.clock_synced", "cycle.start", "npc.found",
                    "shop.opened"}
GAP_SECONDS = 120.0


def truncated(ep) -> bool:
    """True when this episode is cut short by the process going away."""
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

    # The first episode begins wherever the corpus does, which is mid-flow.
    if n == 0:
        continue
    if truncated(ep):
        truncated_n += 1
        continue

    # 1. A CANCEL THAT COMMITTED MUST LOCATE THE ITEM BEFORE LISTING IT.
    #    register_item identifies what it is listing by diffing the inventory,
    #    and inventory.returned is that diff's result. Listing before it means
    #    listing a slot nobody has identified -- the path that can commit real
    #    money to a decision nobody made.
    c = first(seq, "cancel.committed")
    ret = first(seq, "inventory.returned")
    reg = first(seq, "register.before_load")
    if c is not None and reg is not None:
        check(ret is not None and c < ret < reg,
              f"{where}: cancel.committed -> register.before_load with no "
              f"inventory.returned between them ({' -> '.join(seq)})")

    # 2. AN ABORTED CANCEL MUST NEVER REACH A REGISTRATION.
    #    cancel.aborted means the withdrawal did not complete. Listing after it
    #    would list an item that is still on the market, or one whose location
    #    is unknown.
    ab = first(seq, "cancel.aborted")
    com = first(seq, "register.committed")
    if ab is not None:
        check(com is None or com < ab,
              f"{where}: register.committed AFTER cancel.aborted -- a listing "
              f"was made off a cancel that did not complete "
              f"({' -> '.join(seq)})")

    # 3. NOTHING IS PRICED THAT WAS NOT READ.
    #    register.committed is the irreversible step. The price it commits has
    #    to come from a suggestion that was actually read this episode, or the
    #    number was invented.
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

    # 4. A COMMITTED CANCEL MUST NOT END THE EPISODE SILENTLY.
    #    Once the item is out of the shop it is sitting in the work tab. The
    #    episode has to end in a listing, or in a marker saying it is carried,
    #    or the item is stranded with nothing recording that it is.
    if c is not None:
        ended_ok = (com is not None
                    or "worktab.carrying" in seq
                    or "relist.stranded" in seq)
        check(ended_ok,
              f"{where}: the cancel COMMITTED and the episode ends without a "
              f"registration or a strand marker -- an item left the shop and "
              f"nothing records where it went ({' -> '.join(seq)})")

    # 5. THE ORDER WITHIN A CANCEL IS FIXED.
    #    Change is clicked, then the confirmation. A committed cancel with no
    #    preceding click on Change means the sequence was entered mid-way.
    if c is not None:
        bc = first(seq, "cancel.before_change")
        check(bc is not None and bc < c,
              f"{where}: cancel.committed with no cancel.before_change before "
              f"it ({' -> '.join(seq)})")

    # 6. EVERY EPISODE STARTS AT ITS TARGET.
    check(seq[0] in ("table.target",) or n == 0,
          f"{where}: episode does not begin at table.target (begins {seq[0]!r})")


# ==========================================================================
# ACROSS episodes: the transitions that appear at all.
# ==========================================================================
pairs = collections.Counter()
for ep in episodes:
    seq = labels(ep)
    for a, b in zip(seq, seq[1:]):
        pairs[(a, b)] += 1

# A cancel's confirmation must never be immediately followed by a registration
# commit: the item has to be found in the inventory first. This is rule 1 as a
# pure adjacency, which catches an episode boundary being drawn wrongly too.
check(("cancel.committed", "register.committed") not in pairs,
      "cancel.committed is immediately followed by register.committed "
      "somewhere -- nothing located the item in between")

check(("cancel.aborted", "register.committed") not in pairs,
      "cancel.aborted is immediately followed by register.committed somewhere")

# The corpus must actually contain the failure shapes, or this suite is only
# testing the happy path and would stay green through a regression that only
# affects recovery.
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

# If EVERY episode were truncated there would be nothing left to check, and
# this suite would pass by having tested nothing at all.
check(truncated_n < len(episodes) // 2,
      f"{truncated_n} of {len(episodes)} episodes are truncated -- too much of "
      f"the corpus is the tail of a killed run to draw conclusions from")

print(f"sequence_test: {checks} checks, {len(failures)} failure(s)")
for line in failures[:20]:
    print("  FAIL", line)
if len(failures) > 20:
    print(f"  ... and {len(failures) - 20} more")
sys.exit(1 if failures else 0)
