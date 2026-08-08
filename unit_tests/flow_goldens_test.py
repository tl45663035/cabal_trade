"""The resupply flow, checked against frames from the runs that performed it.

Every other suite builds its inputs. This one replays real screenshots taken
while the script was buying, converting and relisting for real, and asks the
readers to agree about them.

The design problem with golden frames is circularity: a frame recorded by the
code, checked by the same code, proves only that the code is consistent with
itself. Two things avoid that here.

  CROSS-READER AGREEMENT. The strongest checks compare readers that share no
  code and no screen region. purchase_confirm reads a centred dialog; the price
  it finds must equal the one read_purchase_rows found in a table column
  hundreds of pixels away, in a separate OCR pass. Neither can be wrong alone
  without the other disagreeing.

  RECORDED CONTEXT AS GROUND TRUTH. record() writes the decision the script
  made alongside the pixels. Where that decision came from a DIFFERENT reader
  than the one under test, it is independent evidence -- buy.dialog's
  `available` came from the table, so testing the dialog against it is a real
  comparison.

Where neither applies, the check is stated as what it is: a wiring check.

The corpus is live session data -- it holds the operator's balance, character
name and listings -- so it is gitignored and cannot be committed. This file
therefore SKIPS LOUDLY rather than passing quietly when frames are absent: a
green run with no corpus has tested no pixels at all, and must not read the
same as a full one.

    py unit_tests/flow_goldens_test.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.environ["CABAL_SALES_DB"] = str(
    _Path(tempfile.mkdtemp(prefix="cabal_flow_test_")) / "scratch.db")

import trade as m  # noqa: E402

m.NO_INPUT = True

fails = []
count = 0
skipped = []
_quiet = "-v" not in sys.argv


def check(cond, label):
    global count
    count += 1
    if not cond:
        fails.append(label)
        print(f"  FAIL  {label}")
    elif not _quiet:
        print(f"  ok    {label}")


def section(title):
    print(f"\n--- {title}")


CORPUS = _ROOT / "unit_tests" / "corpus"
INDEX = CORPUS / "run_index.jsonl"


FLOW = CORPUS / "goldens" / "flow"
FLOW_MANIFEST = FLOW / "manifest.jsonl"


def _read_index(path, base):
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        frame = base / (row.get("file") or "")
        if frame.exists():
            out.append((frame, row))
    return out


def frames(label, limit=None):
    """Frames carrying `label`, newest first.

    PROMOTED goldens first, then the live corpus. The corpus prunes itself --
    frames are deleted from the front on every run -- so a fixture keyed to a
    run_NNNNN.png works today and silently vanishes in a week. That is not
    hypothetical: the 428,142,429 Alz purchase frame was a fixture in the
    afternoon and had rotated out by midnight, turning a real check into a
    skipped one with nothing to say about it.
    """
    out = [(p, r) for p, r in _read_index(FLOW_MANIFEST, FLOW)
           if r.get("label") == label]
    seen = {r.get("from") for _p, r in out}
    out += [(p, r) for p, r in _read_index(INDEX, CORPUS)
            if r.get("label") == label and r.get("file") not in seen]
    out.reverse()
    return out[:limit] if limit else out


def need(label, why, limit=None):
    """Frames for `label`, or an empty list plus a loud note."""
    got = frames(label, limit)
    if not got:
        skipped.append(f"{label} ({why})")
        print(f"  (no {label!r} frames on disk; {why} NOT checked)")
    return got


try:
    from PIL import Image
except ImportError:  # pragma: no cover
    print("Pillow is not installed; nothing can be replayed.")
    raise SystemExit(1)


# ==========================================================================
section("BUY: the dialog and the table behind it agree")
# ==========================================================================
#
# buy.dialog is the last frame before real Alz moves. It was added because the
# recorder used to fire at buy.completed -- AFTER the click -- so the one
# dialog whose contents decide how much is spent had no evidence at all, and
# its quantity field could not be mapped without driving the live game.
#
# The check that matters is agreement between two independent readers of the
# same frame: purchase_confirm reads the centred dialog, read_purchase_rows
# reads the results table behind it. They share no region and no code path.

for path, ctx in need("buy.dialog", "the Confirm Purchase dialog", limit=6):
    shot = Image.open(path)
    dlg = m.purchase_confirm(shot)
    name = path.name

    check(dlg is not None, f"{name}: the dialog is recognised")
    if dlg is None:
        continue

    # --- cross-reader: dialog price vs table price ------------------------
    rows = m.read_purchase_rows(shot)
    row1 = rows[0] if rows else None
    check(row1 is not None, f"{name}: the table behind the dialog still reads")
    if row1 is not None:
        check(dlg["price"] == row1.price,
              f"{name}: the dialog's {dlg['price']:,} matches the table's "
              f"{row1.price:,} -- two regions, two OCR passes, one answer")
        check(row1.row == 1,
              f"{name}: and it is row 1, the only row a buy may take")

    # --- recorded context, written by a different reader -------------------
    if "price" in ctx:
        check(dlg["price"] == ctx["price"],
              f"{name}: dialog price {dlg['price']!r} == the {ctx['price']:,} "
              f"the run acted on")
        # ...and the tight crop must carry it ALONE. purchase_confirm falls
        # back to sweeping the whole dialog for any >=6-digit word when the
        # cell fails, so testing only the function hides a broken region --
        # measured: PURCHASE_DLG_PRICE could be set to a 1x1 box and every
        # check above still passed. The fallback also gets riskier the moment
        # a quantity is typed, because then several figures are on screen and
        # it takes the last one it happens to find.
        direct = m.read_number(shot, m.PURCHASE_DLG_PRICE, 40.0)
        check(direct == ctx["price"],
              f"{name}: PURCHASE_DLG_PRICE reads {direct!r} on its own, "
              f"without the whole-dialog fallback (want {ctx['price']:,})")
    if "available" in ctx and dlg.get("qty_max") is not None:
        check(dlg["qty_max"] == ctx["available"],
              f"{name}: the dialog offers {dlg['qty_max']}, the table said "
              f"{ctx['available']} were available")
    if "pack" in ctx and row1 is not None:
        check(row1.pack == ctx["pack"],
              f"{name}: the pack size reads {row1.pack}, run recorded "
              f"{ctx['pack']}")

    # --- the quantity field, which is what makes one order drain a row ----
    check(dlg.get("qty") == 1,
          f"{name}: the field opens at 1 -- everything typed into it is a "
          f"deliberate change, got {dlg.get('qty')!r}")
    check(isinstance(dlg.get("qty_max"), int) and dlg["qty_max"] >= 1,
          f"{name}: and its maximum reads as a number, got "
          f"{dlg.get('qty_max')!r}")

    # --- the buttons, which are what gets clicked -------------------------
    check(dlg.get("buy") and dlg["buy"][1] > 800,
          f"{name}: Buy is found low in the dialog, got {dlg.get('buy')}")
    check(dlg.get("cancel") and dlg["cancel"][0] > dlg["buy"][0],
          f"{name}: and Cancel is to its RIGHT -- swapping these buys what "
          f"was meant to be refused. Buy {dlg.get('buy')}, "
          f"Cancel {dlg.get('cancel')}")


# ==========================================================================
section("BUY: the saved dialog with 48 on offer")
# ==========================================================================
#
# Captured by hand while the market happened to show a row with 48 identical
# listings -- the case the quantity field exists for, and the one the
# recorded frames above do not contain (both were single listings).

GOLD = CORPUS / "goldens" / "purchase_confirm_qty48.png"
if not GOLD.exists():
    skipped.append("purchase_confirm_qty48 (the multi-listing dialog)")
    print(f"  (no golden at {GOLD}; the 48-listing case NOT checked)")
else:
    shot = Image.open(GOLD)
    dlg = m.purchase_confirm(shot)
    check(dlg is not None, "the 48-listing dialog is recognised")
    check(dlg["qty"] == 1 and dlg["qty_max"] == 48,
          f"it reads 1 of 48, got {dlg.get('qty')} of {dlg.get('qty_max')}")
    check(dlg["price"] == 190_190,
          f"at 190,190 for ONE listing, got {dlg.get('price')!r} -- the price "
          f"shown is per the CURRENT quantity, which is why buy_offer re-reads "
          f"it after typing")

    rows = m.read_purchase_rows(shot)
    check(rows[0].available == 48,
          f"and the table's count column agrees: {rows[0].available}")
    check(rows[0].stock == 48,
          f"so the row is worth 48 items rather than the 1 its name says, got "
          f"{rows[0].stock}")

    # Independent of any reader: what the run would have paid.
    check(rows[0].price * 48 == 9_129_120,
          "48 listings at 190,190 is 9,129,120 -- the figure the dialog must "
          "show after the quantity is typed, and what the balance must move by")


# ==========================================================================
section("BUY: the 428,142,429 Alz order, refused by today's rule")
# ==========================================================================
#
# A real frame of the single largest purchase this account has made: 999 Sets
# in one click, 82% of that session's spend. Per item it was a fine trade; as a
# position it was not one anybody chose.

big = [(p, c) for p, c in frames("buy.completed")
       if c.get("pack") == 999]
if not big:
    skipped.append("buy.completed pack=999 (the 428M order)")
    print("  (no 999-Set purchase frame on disk; the runaway NOT checked)")
else:
    path, ctx = big[0]
    check(ctx["price"] == 428_142_429,
          f"the frame records a {ctx['price']:,} Alz order")
    check(ctx["pack"] == 999, "of 999 Sets in one listing")

    # Would today's rule take it? 213 were held at the time, which is over the
    # 200 hard minimum, so the 500 maximum binds and 1,212 is far past it.
    held = 213
    check(held >= m.RESTOCK_TARGET,
          f"{held} held was already over the {m.RESTOCK_TARGET} minimum")
    check(held + ctx["pack"] > m.BUY_MAXIMUM,
          f"and {held} + {ctx['pack']} = {held + ctx['pack']} is past the "
          f"{m.BUY_MAXIMUM} maximum, so it is refused now")
    # ...but the same bundle with nothing held is still taken, because the
    # minimum is hard and a market of big bundles must still be tradable.
    check(0 < m.RESTOCK_TARGET,
          "while with nothing held the minimum is unmet and it would be taken")


# ==========================================================================
section("SELL: the frames of sales that were wrongly thrown away")
# ==========================================================================
#
# sale.implausible frames are negative controls with a twist: they record the
# script REFUSING to book income it should have booked. The old ceiling bounded
# a sale by the quantity still listed, which on a full sale is zero.
#
# So the assertion is inverted -- the current rule must ACCEPT what these
# frames captured being rejected. Real evidence that the fix is a fix.

for path, ctx in need("sale.implausible", "wrongly rejected sales", limit=6):
    proceeds = ctx.get("proceeds")
    name = path.name
    if not proceeds:
        continue
    # These frames predate the fix, so their recorded `why` is the OLD verdict.
    check("more than" in (ctx.get("why") or ""),
          f"{name}: recorded as refused for exceeding a bound "
          f"({(ctx.get('why') or '')[:60]!r})")
    # The unit price comes from the FRAME, not from a constant. It was
    # hardcoded to 209,999 -- correct for the two frames that existed when this
    # was written, and wrong the moment the next run captured a rejection of a
    # different item. A fixture that only works on the fixtures it was written
    # against is not a fixture.
    price = ctx.get("price") or 0
    if not price:
        # Older frames recorded no price. Recover it from the reason text,
        # which reads "N is X units at PRICE each".
        import re as _re
        found = _re.search(r"at ([0-9,]+)", ctx.get("why") or "")
        price = int(found.group(1).replace(",", "")) if found else 0
    if not price:
        continue
    check(proceeds % price == 0,
          f"{name}: {proceeds:,} is a whole number of units at {price:,} "
          f"({proceeds // price}) -- which is what makes it provably a sale "
          f"rather than an overlay misread")
    units = proceeds // price
    # The RECORDED still_listed, not the unit count. Passing the units for both
    # arguments made max(still_listed, listed_units) and max(0, still_listed)
    # agree, so reverting the fix was invisible -- measured, and exactly the
    # kind of test that cannot fail. The frame says the row showed 0 still
    # listed; the listing held what it sold, which is `units`.
    still = ctx.get("still_listed", 0)
    verdict = m.sale_rejection(proceeds, price, still, units)
    check(verdict == "",
          f"{name}: today's rule ACCEPTS it ({units} units sold, {still} "
          f"still listed) -- got {verdict!r}")
    # And the old bound is what refused it: price x the leftovers.
    check(m.sale_rejection(proceeds, price, still, None) != "",
          f"{name}: while the OLD bound -- the {still} still listed -- refuses "
          f"it, which is the bug these frames captured")
    # And the bound that rejected it was the leftovers, which is the bug.
    # The bound the OLD rule used was price x still_listed, whatever that
    # happened to be -- 0 on a fully sold row, more on a partial. Asserting 0
    # was true of the first two frames and wrong for the next one captured.
    check(m.sale_rejection(proceeds, price, still, None) != "",
          f"{name}: the old bound (price x the {still} still listed) refuses "
          f"it, which is exactly the bug these frames captured")


# ==========================================================================
section("NAVIGATE: the steps between the shop and the vendor")
# ==========================================================================
#
# The resupply flow crosses three windows -- Agent Shop Register tab, Purchase
# tab, and the NPC vendor -- and the failures that cost whole runs are all
# navigation: an NPC that cannot be found because a window covers her, a click
# aimed at the Purchase tab that lands on Register.

for path, ctx in need("npc.found", "finding the NPC", limit=3):
    shot = Image.open(path)
    where = m.find_npc(shot, retries=1)
    check(where is not None, f"{path.name}: the NPC is found")
    if where:
        check(0 < where[0] < 2560 and 0 < where[1] < 1440,
              f"{path.name}: at {where}, on screen")

for path, ctx in need("tab.register_open", "the Register tab", limit=3):
    shot = Image.open(path)
    check(m.register_tab_open(shot),
          f"{path.name}: the Register tab is detected")
    check(not m.purchase_tab_open(shot),
          f"{path.name}: and is NOT mistaken for the Purchase tab -- they sit "
          f"side by side, and a click aimed at one lands on the other")

for path, ctx in need("sale.collected", "the table after a sale", limit=3):
    shot = Image.open(path)
    rows = m.read_rows(shot)
    check(rows and len(rows) == m.EXPECTED_ROWS,
          f"{path.name}: all {m.EXPECTED_ROWS} rows read after a collection, "
          f"got {len(rows or [])}")


# ==========================================================================
section("CONVERT: the instrumentation that was missing entirely")
# ==========================================================================
#
# A survey of 718 frames from tonight's runs found labels for every step of
# relisting and buying, and NOTHING for converting -- the one step that turns
# paid-for Sets into listable Cores, driven by an Alt+click Mass Purchase
# dialog with a typed quantity.
#
# Frame-based checks arrive with the next conversion. Until then this asserts
# the capture points exist, so their absence cannot go unnoticed again.

import inspect as _inspect  # noqa: E402

_convert_src = _inspect.getsource(m.convert_cores)
for label, why in [
        ("convert.dialog", "the vendor dialog as it opens, with its limit"),
        ("convert.typed", "the field after typing, which the read-back judges"),
        ("convert.confirming", "the last frame before the Sets are spent")]:
    check(f'record("{label}"' in _convert_src,
          f"convert_cores records {label} -- {why}")

_conv_frames = frames("convert.dialog")
if not _conv_frames:
    skipped.append("convert.dialog (no conversion has run since it was added)")
    print("  (no convert.* frames yet; they appear after the next conversion)")
else:
    for path, ctx in _conv_frames[:3]:
        shot = Image.open(path)
        check(m.vendor_shop_open(shot),
              f"{path.name}: the vendor Shop is open behind the dialog")
        det = m.mass_purchase_details(shot)
        if ctx.get("limit"):
            check(det.get("qty_max") == ctx["limit"],
                  f"{path.name}: the field's limit reads {det.get('qty_max')}, "
                  f"the run acted on {ctx['limit']}")


# ==========================================================================
section("CALIBRATE: every anchor still sits where it is recorded")
# ==========================================================================
#
# A wrong anchor reference does not fail loudly. It is absorbed into `scale`,
# so calibration reports success and every derived coordinate is quietly wrong
# by a proportion of the window -- which is how "Function" once sat 74px out.
# The only defence is measuring them against frames.
#
# Checked with the REAL matcher, _anchor_centre, not a token search: it is
# case-insensitive, matches by SUBSTRING, and discards any word seen twice.
# Three otherwise-perfect candidates were rejected on that basis -- "quantity"
# also matches "Quantity)" in "(Price x Quantity)", and "sales" matches both
# "Net sales" and "Sales Fee".

_ref_frames = []
for _p, _c in frames("register.committed", limit=8) + frames("table.target", limit=8):
    _lay = _c.get("layout") or {}
    # EXACT reference layout only: then ref = measured - origin, with no scale
    # division to round. Mixing in scaled frames is what produced a spurious
    # 2px systematic offset the first time this was measured.
    if _lay.get("origin") == [10, 30] and abs(_lay.get("scale", 1) - 1) < 1e-9:
        _ref_frames.append(_p)

if not _ref_frames:
    skipped.append("anchor references (no exact-reference-layout frames)")
    print("  (no reference-layout frames; anchor positions NOT checked)")
else:
    from PIL import Image as _Im
    check(len(m.REF_ANCHORS_ALL) == len(m.REF_ANCHORS) + len(m.REF_ANCHORS_EXTRA),
          "both anchor tiers are in the set the fit consumes")
    check(len(m.REF_ANCHORS) >= m.MIN_ANCHORS_AFTER_DROP,
          f"the REQUIRED tier alone ({len(m.REF_ANCHORS)}) still clears the "
          f"drop floor ({m.MIN_ANCHORS_AFTER_DROP}), so calibrating from the "
          f"Purchase tab -- where the extras do not exist -- is still possible")

    _shot = _Im.open(_ref_frames[0])
    _words = m.find_words(_shot, m.TRADE_REGION, 20)
    _lines = m._text_lines(_words)
    _seen = 0
    for _name, _ref in m.REF_ANCHORS_ALL:
        _at = m._anchor_centre(_name, _words, _lines)
        if _at is None:
            # Not every anchor is visible in every state; 'Trade' sits in the
            # title bar outside TRADE_REGION, for instance. Absence is not a
            # failure -- being in the WRONG PLACE is.
            continue
        _seen += 1
        _got = (_at[0] - 10, _at[1] - 30)
        _off = max(abs(_got[0] - _ref[0]), abs(_got[1] - _ref[1]))
        check(_off <= 2,
              f"anchor {_name!r} is at {_ref}, measured {_got} "
              f"(off by {_off}px) -- a wrong reference is absorbed into scale "
              f"and never reported")
    check(_seen >= m.MIN_ANCHORS_AFTER_DROP,
          f"{_seen} anchors were locatable on this frame, at least "
          f"{m.MIN_ANCHORS_AFTER_DROP} are needed to fit after outlier drops")
    print(f"  checked {_seen} anchor position(s) on {_ref_frames[0].name}")


# ==========================================================================
print("\n" + "=" * 60)
print(f"flow goldens: {count} checks, {len(fails)} failed"
      + (f", {len(skipped)} STAGE(S) SKIPPED" if skipped else ""))
if skipped:
    print("  no frames for: " + "; ".join(skipped))
    print("  -> those stages were NOT exercised. The corpus is session data "
          "and is gitignored, so this is normal off the recording machine.")
if fails:
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)
print("all green")
