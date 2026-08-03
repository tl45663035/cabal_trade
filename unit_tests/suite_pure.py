"""Combinatorial coverage of every pure decision in trade.

These are the functions that decide what price to list at, which row to act on,
and what a number says -- i.e. everywhere a wrong answer costs money. Each
block sweeps a real matrix rather than sampling, so the counts below are
distinct cases, not repetitions.

Run: py suite_pure.py       (prints a per-block tally and a total)
"""

import itertools
import random
import sys

from pathlib import Path as _Path  # noqa: E402
_ROOT = _Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import trade as m  # noqa: E402

VIP = "Yekaterina VIP Membership Use Period: 30 days"
# Derived from the table, never hard-coded: this suite asserted a literal
# 105,000,000 and would have gone on passing against the old number after the
# floor was raised, testing nothing.
FLOOR = m.item_price_floor(VIP)
STOCK = ["Upgrade Core (Ultimate)", "Upgrade Core(High)", "Upgrade Core(Highest)",
         "Force Core(High)", "Force Core(Highest)", "Mana Absorb Bracelet",
         "Archridium Coat (FB)", "Vampiric Earring +8", "Archridium Plate(GL)"]

RAN = 0
BAD = []


def ok(cond, label):
    global RAN
    RAN += 1
    if not cond:
        BAD.append(label)


def block(name, before):
    print(f"  {name:44} {RAN - before:6,d} cases")


# ---------------------------------------------------------------- pricing --
start = RAN
# Boundaries expressed relative to FLOOR so they keep straddling it when the
# floor moves; the fixed values around it are real market prices off the table.
MARKETS = [0, 1, 999, 1_000, 85_000, 410_000, 10_000_000,
           FLOOR - 1, FLOOR, FLOOR + 1, 250_000_000, 10**12]
FLOORS = [0, FLOOR]
CLI_FLOORS = [None, 0, 50_000_000, 200_000_000]
for market, floor, cli in itertools.product(MARKETS, FLOORS, CLI_FLOORS):
    try:
        price, _ = m.choose_price(market, cli or 0, None, floor)
    except m.Aborted:
        # Only the explicit --floor may refuse.
        ok(bool(cli) and market > 0 and market < cli,
           f"choose_price refused unexpectedly {market}/{cli}/{floor}")
        continue
    if floor:
        ok(price >= floor, f"floor breached: market={market} -> {price}")
    if market <= 0:
        ok(price >= m.FALLBACK_PRICE or price == floor,
           f"no market price should fall back: {market} -> {price}")
    ok(price > 0, f"non-positive price {price}")
block("choose_price matrix", start)

# ------------------------------------------------------------ floor names --
start = RAN
CONFUSE = {"V": "YUWN", "I": "T1l|!i", "P": "FRBD", "M": "NH", "e": "co",
           "a": "oe", "r": "n", "n": "rm", "b": "h", "s": "5", "t": "f",
           "0": "O", "3": "8", "y": "vg", "k": "lc", "d": "cl", "u": "vn",
           "i": "1l", "o": "0c", "m": "nr", "h": "bn", "p": "qg"}


def corrupt_at(text, i, replacement):
    return text[:i] + replacement + text[i + 1:]


# Every single-character substitution of the VIP name that the font can produce.
for i, ch in enumerate(VIP):
    for repl in CONFUSE.get(ch, ""):
        ok(m.item_price_floor(corrupt_at(VIP, i, repl)) == FLOOR,
           f"VIP floor lost: {corrupt_at(VIP, i, repl)!r}")
# Every single deletion.
for i in range(len(VIP)):
    ok(m.item_price_floor(VIP[:i] + VIP[i + 1:]) == FLOOR,
       f"VIP floor lost on deletion at {i}")
# Every single insertion of a stroke-like glyph.
for i in range(0, len(VIP), 2):
    for ins in "il1|":
        ok(m.item_price_floor(VIP[:i] + ins + VIP[i:]) == FLOOR,
           f"VIP floor lost on insertion at {i}")
# No non-VIP item may EVER gain a floor, under the same corruptions.
for name in STOCK:
    ok(m.item_price_floor(name) == 0, f"clean {name!r} gained a floor")
    for i, ch in enumerate(name):
        for repl in CONFUSE.get(ch, ""):
            bad = corrupt_at(name, i, repl)
            ok(m.item_price_floor(bad) == 0, f"{bad!r} gained a floor")
    for i in range(len(name)):
        ok(m.item_price_floor(name[:i] + name[i + 1:]) == 0,
           f"{name!r} minus char {i} gained a floor")
block("item_price_floor corruption sweep", start)

# ------------------------------------------------------------ number reads --
start = RAN
for value in [0, 1, 2, 9, 10, 23, 60, 65, 99, 158, 162, 210, 220, 250, 9999,
              85_000, 410_000, 118_999_999, 10_000_000_000]:
    text = f"{value:,}"
    ok(m._digits(text) == value, f"_digits({text!r})")
    ok(m._digits(text + " Alz") == value, f"_digits with suffix {text!r}")
    ok(m._price_value(text + " Alz") == value, f"_price_value({text!r})")
    for junk in ["", " ", "\n", "\t"]:
        ok(m._digits(junk + text + junk) == value, f"_digits padded {text!r}")
# "0 Alz" arrives with no digits at all.
for zeroish in ["OAlz", "OAIz", "0Alz", "O Alz", "0 Alz"]:
    ok(m._price_value(zeroish) == 0, f"_price_value({zeroish!r}) should be 0")
# Nothing numeric at all must not become a price.
for junk in ["Alz", "", "   ", "Change", "On Sale", "Refresh"]:
    ok(m._price_value(junk) in (None, 0), f"_price_value({junk!r})")
block("digit and price parsing", start)

# ------------------------------------------------------- name canonicalising --
start = RAN
for name in [VIP] + STOCK:
    ok(m._canonical(name) == m._canonical(name), f"_canonical stable {name!r}")
    ok(m._canonical(name.upper()) == m._canonical(name.lower()),
       f"_canonical case-insensitive {name!r}")
    ok(m._floor_key(name) == m._floor_key(name), f"_floor_key stable {name!r}")
# +6 and +9 must never collide -- folding digits once made them equal.
for a, b in itertools.combinations("0123456789", 2):
    ok(m._canonical(f"Blade +{a}") != m._canonical(f"Blade +{b}"),
       f"+{a} collided with +{b}")
# (FA) vs (FB) must stay distinct.
for a, b in itertools.combinations("ABCD", 2):
    ok(m._canonical(f"SIGMetal Headgear (F{a})")
       != m._canonical(f"SIGMetal Headgear (F{b})"), f"F{a} vs F{b}")
block("name canonicalisation", start)

# ---------------------------------------------------------- row identity --
start = RAN


def row(index, name, qty, price, action="change"):
    top = index * 79
    return m.Row(index=index, name=name, change=(1126, top + 20), top=top,
                 bottom=top + 40, action=action, price=price, qty=qty)


QTYS = [None, 1, 2, 60, 162, 210, 220]
PRICES = [None, 85_000, 238_700, 410_000, 118_999_999]
# A two-row table of same-named stacks, swept over every qty/price combination.
for q1, q2, p1, p2 in itertools.product(QTYS, QTYS, PRICES, PRICES):
    table = [row(1, "Force Core(High)", q1, p1), row(2, "Force Core(High)", q2, p2)]
    for target in (1, 2):
        ref = m.RowRef.of(table[target - 1], table)
        found, note = m.locate_row(table, ref)
        ok(found is not None, f"lost a row entirely q={q1},{q2} p={p1},{p2}")
        if found is not None and (q1, p1) != (q2, p2):
            # Distinguishable rows must resolve to the right one.
            distinct = (q1 != q2 and None not in (q1, q2)) or \
                       (p1 != p2 and None not in (p1, p2))
            if distinct:
                ok(found.index == target,
                   f"wrong row q={q1},{q2} p={p1},{p2} target={target}")
block("locate_row duplicate matrix", start)

start = RAN
# A genuinely absent row is 'missing'; a flaked one is 'unmatched'.
for name in [VIP] + STOCK:
    table = [row(1, name, 10, 1_000_000)]
    other = [row(1, "Completely Different Item", 10, 1_000_000)]
    _, note = m.locate_row(other, m.RowRef(name, 10, 1_000_000))
    ok(note == "missing", f"{name!r} vs unrelated table -> {note}")
    _, note = m.locate_row([], m.RowRef(name, 10, 1_000_000))
    ok(note == "missing", f"{name!r} vs empty table -> {note}")
    found, note = m.locate_row(table, m.RowRef(name, 10, 1_000_000))
    ok(found is not None and note == "", f"{name!r} exact match -> {note}")
    # One substituted character anywhere must read as 'unmatched', never gone.
    for i, ch in enumerate(name):
        for repl in CONFUSE.get(ch, "")[:1]:
            flaked = [row(1, corrupt_at(name, i, repl), 10, 1_000_000)]
            found, note = m.locate_row(flaked, m.RowRef(name, 10, 1_000_000))
            ok(note != "missing",
               f"flaked {name!r}@{i} read as MISSING (would report sold out)")
block("missing vs unmatched", start)

# ---------------------------------------------------------------- layout --
start = RAN
GEOMETRY = sorted(set(m._TRADE_FRAME_GEOMETRY) | set(m._INVENTORY_FRAME_GEOMETRY)
                  | set(m._CLIENT_FRAME_GEOMETRY))
reference = {n: getattr(m, n) for n in GEOMETRY}
for scale in [1.0, 0.7895, 0.75, 0.6, 0.5, 1.25, 2.0]:
    for origin in [(10, 30), (327, 197), (0, 0), (800, 400)]:
        client = (0, 0, int(2560 * scale), int(1440 * scale))
        layout = m.Layout(screen=(2560, 1440), origin=origin, scale=scale,
                          client=m.REF_CLIENT)
        m.apply_layout(layout)
        for name in GEOMETRY:
            value = getattr(m, name)
            if isinstance(value, tuple) and len(value) == 4 and \
                    all(isinstance(v, int) for v in value):
                ok(value[2] > value[0] and value[3] > value[1],
                   f"{name} inverted at scale={scale} origin={origin}")
                ok(0 <= value[0] and 0 <= value[1],
                   f"{name} negative at scale={scale} origin={origin}")
m.apply_layout(m.Layout(screen=m.REF_SCREEN, origin=m.REF_TRADE_ORIGIN,
                        scale=1.0, client=m.REF_CLIENT))
for name in GEOMETRY:
    ok(getattr(m, name) == reference[name],
       f"{name} is not identical after a round trip")
block("apply_layout scale/origin matrix", start)

# ------------------------------------------------------------- dialog kind --
start = RAN
TITLES = {"receipt": ["Confirm", "Receipt"],
          "confirm": ["Cancel", "item", "registration", "Confirmation"],
          "extension": ["Registration", "Extension"]}
SWAPS = {"i": "l", "o": "0", "s": "5", "e": "c", "n": "r", "t": "f", "a": "o"}
for kind, words in TITLES.items():
    for i in range(len(words)):
        for ch, repl in SWAPS.items():
            variant = list(words)
            variant[i] = variant[i].replace(ch, repl, 1)
            texts = [m._normalise(w) for w in variant]
            texts.append(m._normalise("".join(variant)))
            got = ("receipt" if m._mentions(texts, "receipt") else
                   "confirm" if m._mentions(texts, "confirmation") else
                   "extension" if (m._mentions(texts, "extension")
                                   or m._mentions(texts, "registration")) else None)
            ok(got == kind, f"{kind}: {variant} -> {got}")
# Table chrome must never look like a dialog.
for words in [["Change"], ["Receive"], ["Register"], ["Refresh"], ["On", "Sale"],
              ["Register", "Item"], ["Register", "QTY"], ["registered"],
              ["Name"], ["QTY"], ["Price"], ["Status"], ["Function"],
              ["Selling"], ["Expired"], ["Sold"], ["Total", "Quantity"]]:
    texts = [m._normalise(w) for w in words] + [m._normalise("".join(words))]
    ok(not (m._mentions(texts, "receipt") or m._mentions(texts, "confirmation")
            or m._mentions(texts, "extension")
            or m._mentions(texts, "registration")),
       f"table chrome {words} read as a dialog")
block("dialog classification", start)

# ------------------------------------------------------------- row specs --
start = RAN
for spec, want in [("1", [1]), ("1-3", [1, 2, 3]), ("1,3,5", [1, 3, 5]),
                   ("1-10", list(range(1, 11))), ("5-5", [5]),
                   ("2 4 6", [2, 4, 6]), ("1-2,5", [1, 2, 5])]:
    ok(m.parse_row_spec([spec]) == want, f"parse_row_spec({spec!r})")
for bad in ["3-1", "10-1"]:
    try:
        m.parse_row_spec([bad])
        ok(False, f"parse_row_spec({bad!r}) should refuse")
    except ValueError:
        ok(True, "")
block("row spec parsing", start)

# ------------------------------------------------------------ NPC sweep --
start = RAN
for scale in [1.0, 0.7895, 0.5, 2.0]:
    m.apply_layout(m.Layout(screen=(2560, 1440), origin=(10, 30), scale=scale,
                            client=m.REF_CLIENT))
    offsets = m._npc_click_offsets()
    ok(len(offsets) == len(set(offsets)), f"duplicate sweep points at {scale}")
    ok(all(y > 0 for _, y in offsets), f"sweep clicks above the label at {scale}")
    cx, cy = m.NPC_BODY_OFFSET
    dist = [(4 * (x - cx) ** 2 + (y - cy) ** 2) ** 0.5 for x, y in offsets]
    ok(dist == sorted(dist), f"sweep not ordered outward at {scale}")
m.apply_layout(m.Layout(screen=m.REF_SCREEN, origin=m.REF_TRADE_ORIGIN,
                        scale=1.0, client=m.REF_CLIENT))
block("NPC sweep geometry", start)

# ----------------------------------------------------------------- fuzz --
start = RAN
rng = random.Random(20260803)
for _ in range(4000):
    name = rng.choice([VIP] + STOCK)
    text = list(name)
    for _ in range(rng.randint(1, 3)):
        i = rng.randrange(len(text))
        roll = rng.random()
        if roll < 0.5 and text[i] in CONFUSE:
            text[i] = rng.choice(CONFUSE[text[i]])
        elif roll < 0.8:
            text[i] = ""
        else:
            text.insert(i, rng.choice("il1|"))
    got = m.item_price_floor("".join(text))
    if name == VIP:
        ok(got == FLOOR, f"VIP lost its floor: {''.join(text)!r}")
    else:
        ok(got == 0, f"{name!r} gained a floor as {''.join(text)!r}")
block("randomised name fuzz", start)

print(f"\n  {'TOTAL':44} {RAN:6,d} cases")
print(f"  {'failures':44} {len(BAD):6,d}")
for label in BAD[:25]:
    print(f"    FAIL {label}")
if BAD:
    raise SystemExit(1)
