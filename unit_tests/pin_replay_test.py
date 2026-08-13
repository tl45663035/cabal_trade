"""The 2026-08-13 wrong-row cancel, replayed from the log that recorded it.

DRIVES NOTHING. Pure row-selection arithmetic against the real figures.

From logs/run_2026-08-12_230502.log, cycle 25:

    22722.8   row 12 is at the top of the view after 11 notch(es)
    22722.8   one of 2 identical stacks; taking row 12 by measured position
    22729.9   'Upgrade Core (Ultimate)' moved from row 1 to 9 - following it.
    22729.9   [relist 1/2] row 9: 'Upgrade Core (Ultimate)' at 509,000 Alz

The position was MEASURED -- eleven notches put absolute row 12 at screen 1,
and the log says so in as many words. Then an identity search re-derived it,
landed on screen 9, and the code followed. Screen 9 of a view starting at
absolute 12 is absolute 20, and the shop dump at 22665.2 has absolute 20 at
exactly 509,000. So the game cancelled row 20 while SHOP.cancel(12) emptied
row 12: one row repriced unasked, one never repriced, and a phantom that ended
the run thirteen minutes later.

The search picked wrong because RowRef counts its ordinal over the whole
30-row shop and locate_row then indexes that ordinal into a 10-row view.
"""
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.environ["CABAL_SALES_DB"] = str(
    Path(tempfile.gettempdir()) / "pin_replay_test.db")

sys.argv = ["pin_replay_test"]
import trade  # noqa: E402

PASS = FAIL = 0
CORE = "Upgrade Core (Ultimate)"


def check(ok, why):
    global PASS, FAIL
    if ok:
        PASS += 1
    else:
        FAIL += 1
        print(f"  FAIL  {why}")


def rule(title):
    print("=" * 74)
    print(title)
    print("=" * 74)


def row(index, name, qty, price, action="change"):
    return trade.Row(index=index, name=name, change=(0, 0), top=0, bottom=0,
                     action=action, price=price, qty=qty, status="On Sale")


def empty(index):
    return trade.Row(index=index, name="", change=(0, 0), top=0, bottom=0,
                     action="register", price=None, qty=None, status="")


# The shop as the batch walk read it, 22665.2. Absolute numbering.
CATALOGUE = (
    [empty(i) for i in range(1, 12)]
    + [row(12, CORE, 250, 480_000)]
    + [empty(i) for i in range(13, 19)]
    + [row(19, CORE, 250, 500_000),
       row(20, CORE, 250, 509_000),
       row(21, CORE, 134, 500_000),
       row(22, CORE, 250, 509_000),
       row(23, CORE, 250, 509_933),
       row(24, CORE, 250, 509_933)]
    + [empty(i) for i in range(25, 30)]
    + [row(30, "Master's Archridium Blade", 1, 900_000_000)]
)

# goto_row(12) scrolled eleven notches, so absolute 12 sits at SCREEN 1 and the
# view runs 12..21. This is what _relist_cycle re-reads.
VIEW = [row(screen, r.name, r.qty, r.price)
        if r.name else empty(screen)
        for screen, r in enumerate(CATALOGUE[11:21], start=1)]

ASKED_ABSOLUTE = 12
PINNED_SCREEN = 1


def absolute_of(screen):
    """Screen position -> absolute row, for a view starting at absolute 12."""
    return ASKED_ABSOLUTE + screen - 1


rule("the view is what the log says it was")

check(len(VIEW) == 10, "ten rows visible")
check(VIEW[0].price == 480_000,
      f"screen 1 is the target at 480,000 (got {VIEW[0].price:,})")
check(absolute_of(9) == 20, "screen 9 is absolute row 20")
check(VIEW[8].price == 509_000,
      f"and screen 9 reads 509,000, matching the shop dump's row 20 "
      f"(got {VIEW[8].price:,})")

rule("the identity search picks a row the batch never asked for")

ref = trade.RowRef.of(CATALOGUE[11], CATALOGUE)      # absolute row 12
check(ref.name == CORE, "the ref names the right item")

found, note = trade.locate_row(VIEW, ref)
if found is None:
    check(False, f"the search returned nothing ({note!r})")
else:
    landed = absolute_of(found.index)
    # The reorder to price-first rescues THIS case; the ordinal bug it papers
    # over is why the pin exists. Record which it is, either way.
    if found.index == PINNED_SCREEN:
        check(True, "the search happens to agree here -- the price filter "
                    "separates 480,000 from its siblings")
    else:
        check(True, f"the search disagrees with the measurement: screen "
                    f"{found.index} = absolute {landed}, not {ASKED_ABSOLUTE}")

rule("the pin acts on the row that was measured")

# What _relist_cycle now does when absolute_row is set: take the row at the
# measured screen position and CHECK the name, rather than searching.
target = VIEW[PINNED_SCREEN - 1]
check(absolute_of(target.index) == ASKED_ABSOLUTE,
      f"the pinned row is absolute {ASKED_ABSOLUTE}, the one the batch asked "
      f"for")
check(bool(trade.match_rows([target], ref.name)),
      "and its name agrees, so it is acted on")
check(target.price == 480_000,
      "at 480,000 -- the target's own price, not a sibling's 509,000")

rule("a sibling could never be reached by the pin, whatever it is priced at")

# The six Force Core follows that night were same-name AND same-price
# siblings, where no filter can separate them and only position can.
SAME = [row(i, "Force Core(High)", 100, 250_000) for i in range(1, 11)]
for asked_screen in (1, 3, 6):
    picked = SAME[asked_screen - 1]
    check(picked.index == asked_screen,
          f"screen {asked_screen} of ten identical stacks resolves to itself "
          f"-- position separates what identity cannot")

ident = trade.RowRef.of(SAME[0], SAME)
got, _note = trade.locate_row(SAME, ident)
check(got is not None,
      "the identity search still returns SOMETHING among identical stacks")
check(True, f"  (it returns screen {getattr(got, 'index', None)}; which one "
            f"is unknowable by identity alone, which is the point)")

rule("the model is keyed on the row that was asked for")

model = trade.ShopModel()
model.adopt([(r.index, r) for r in CATALOGUE])
model.enforce = True
check(not model.is_empty(12), "the model holds absolute row 12")
check(not model.is_empty(20), "and absolute row 20")

# The check that runs before the click, against the PINNED row.
try:
    model.check(ASKED_ABSOLUTE, VIEW[PINNED_SCREEN - 1])
    check(True, "checking the pinned row against model slot 12 passes")
except Exception as exc:                                   # noqa: BLE001
    check(False, f"the pinned row failed its own model check: {exc}")

# What the old code did: check absolute 12 against the row at screen 9.
model2 = trade.ShopModel()
model2.adopt([(r.index, r) for r in CATALOGUE])
model2.enforce = True
before = model2.mismatches
model2.check(ASKED_ABSOLUTE, VIEW[8])
check(model2.mismatches > before,
      "checking absolute 12 against screen 9's row is REPORTED as a mismatch "
      "-- 509,000 against the model's 480,000. Name alone said nothing, "
      "which is how it passed unnoticed on the night")

print()
print("-" * 74)
print(f"{PASS + FAIL} checks, {FAIL} failed")
sys.exit(1 if FAIL else 0)
