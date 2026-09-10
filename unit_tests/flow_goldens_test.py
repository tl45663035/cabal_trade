import json
import os
import sys
import tempfile
from pathlib import Path as _Path

_ROOT = _Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.environ["CABAL_SALES_DB"] = str(
    _Path(tempfile.mkdtemp(prefix="cabal_flow_test_")) / "scratch.db")

import trade as m

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
    out = [(p, r) for p, r in _read_index(FLOW_MANIFEST, FLOW)
           if r.get("label") == label]
    seen = {r.get("from") for _p, r in out}
    out += [(p, r) for p, r in _read_index(INDEX, CORPUS)
            if r.get("label") == label and r.get("file") not in seen]
    out.reverse()
    return out[:limit] if limit else out


def need(label, why, limit=None):
    got = frames(label, limit)
    if not got:
        skipped.append(f"{label} ({why})")
        print(f"  (no {label!r} frames on disk; {why} NOT checked)")
    return got


try:
    from PIL import Image
except ImportError:
    print("Pillow is not installed; nothing can be replayed.")
    raise SystemExit(1)


section("BUY: the dialog and the table behind it agree")

for path, ctx in need("buy.dialog", "the Confirm Purchase dialog", limit=6):
    shot = Image.open(path)
    dlg = m.purchase_confirm(shot)
    name = path.name

    check(dlg is not None, f"{name}: the dialog is recognised")
    if dlg is None:
        continue

    rows = m.read_purchase_rows(shot)
    row1 = rows[0] if rows else None
    check(row1 is not None, f"{name}: the table behind the dialog still reads")
    if row1 is not None:
        check(dlg["price"] == row1.price,
              f"{name}: the dialog's {dlg['price']:,} matches the table's "
              f"{row1.price:,} -- two regions, two OCR passes, one answer")
        check(row1.row == 1,
              f"{name}: and it is row 1, the only row a buy may take")

    if "price" in ctx:
        check(dlg["price"] == ctx["price"],
              f"{name}: dialog price {dlg['price']!r} == the {ctx['price']:,} "
              f"the run acted on")
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

    check(dlg.get("qty") == 1,
          f"{name}: the field opens at 1 -- everything typed into it is a "
          f"deliberate change, got {dlg.get('qty')!r}")
    check(isinstance(dlg.get("qty_max"), int) and dlg["qty_max"] >= 1,
          f"{name}: and its maximum reads as a number, got "
          f"{dlg.get('qty_max')!r}")

    check(dlg.get("buy") and dlg["buy"][1] > 800,
          f"{name}: Buy is found low in the dialog, got {dlg.get('buy')}")
    check(dlg.get("cancel") and dlg["cancel"][0] > dlg["buy"][0],
          f"{name}: and Cancel is to its RIGHT -- swapping these buys what "
          f"was meant to be refused. Buy {dlg.get('buy')}, "
          f"Cancel {dlg.get('cancel')}")


section("BUY: the saved dialog with 48 on offer")

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

    check(rows[0].price * 48 == 9_129_120,
          "48 listings at 190,190 is 9,129,120 -- the figure the dialog must "
          "show after the quantity is typed, and what the balance must move by")


section("BUY: the 428,142,429 Alz order, refused by today's rule")

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

    held = 213
    check(held >= m.RESTOCK_TARGET,
          f"{held} held was already over the {m.RESTOCK_TARGET} minimum")
    check(held + ctx["pack"] > m.BUY_MAXIMUM,
          f"and {held} + {ctx['pack']} = {held + ctx['pack']} is past the "
          f"{m.BUY_MAXIMUM} maximum, so it is refused now")
    check(0 < m.RESTOCK_TARGET,
          "while with nothing held the minimum is unmet and it would be taken")


section("SELL: the frames of sales that were wrongly thrown away")

for path, ctx in need("sale.implausible", "wrongly rejected sales", limit=6):
    proceeds = ctx.get("proceeds")
    name = path.name
    if not proceeds:
        continue
    check("more than" in (ctx.get("why") or ""),
          f"{name}: recorded as refused for exceeding a bound "
          f"({(ctx.get('why') or '')[:60]!r})")
    price = ctx.get("price") or 0
    if not price:
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
    still = ctx.get("still_listed", 0)
    verdict = m.sale_rejection(proceeds, price, still, units)
    check(verdict == "",
          f"{name}: today's rule ACCEPTS it ({units} units sold, {still} "
          f"still listed) -- got {verdict!r}")
    if still > 0:
        check(m.sale_rejection(proceeds, price, still, None) != "",
              f"{name}: with {still} still listed and no registration on file, "
              f"the old bound refuses it -- which is the bug the registration "
              f"lookup exists to fix")
    else:
        check(m.sale_rejection(proceeds, price, still, None) == "",
              f"{name}: a row reading 0 still listed carries NO size evidence, "
              f"so it must not be refused for exceeding a ceiling of zero. "
              f"That reading rejected 313,683,417 Alz of real collections.")


section("NAVIGATE: the steps between the shop and the vendor")

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


section("CONVERT: the instrumentation that was missing entirely")

import inspect as _inspect

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


section("CALIBRATE: every anchor still sits where it is recorded")

_ref_frames = []
for _p, _c in frames("register.committed", limit=8) + frames("table.target", limit=8):
    _lay = _c.get("layout") or {}
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
