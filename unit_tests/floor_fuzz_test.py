"""Does the VIP floor survive realistic OCR corruption, without false hits?

The floor is looked up from a name that OCR produced, so the question is not
"does it work on clean text" but "what fraction of realistic misreads lose it".
"""

import random
import sys

from pathlib import Path as _Path  # noqa: E402
_ROOT = _Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

import trade as m  # noqa: E402

FLOOR = m.item_price_floor("Yekaterina VIP Membership")   # derived, never restated
VIP = "Yekaterina VIP Membership Use Period: 30 days"
VIP_SHORT = "Yekaterina VIP Membership"
# The user's actual non-VIP stock. None of these may EVER gain a floor.
STOCK = ["Upgrade Core (Ultimate)", "Upgrade Core(High)", "Upgrade Core(Highest)",
         "Force Core(High)", "Force Core(Highest)", "Mana Absorb Bracelet",
         "Archridium Coat (FB)", "Vampiric Earring +8", "Archridium Plate(GL)"]

# Confusions this font actually produces, per the OCR work in this session.
CONFUSE = {"V": "YUW", "I": "T1l|!", "P": "FRB", "0": "O", "o": "0",
           "1": "il|", "l": "1i", "i": "1l", "e": "c", "c": "e", "a": "o",
           "S": "5", "5": "S", "B": "8", "8": "B", "r": "n", "n": "r",
           "m": "rn", "g": "9", "t": "f", "u": "v", "y": "v"}

fails = []


def check(label, got, want):
    ok = got == want
    if not ok:
        fails.append(label)
    print(f"{'OK  ' if ok else 'FAIL'} {label:56} -> {got!r}")


def corrupt(text, rng, edits=1):
    out = list(text)
    for _ in range(edits):
        i = rng.randrange(len(out))
        ch = out[i]
        roll = rng.random()
        if roll < 0.55 and ch in CONFUSE:
            out[i] = rng.choice(CONFUSE[ch])
        elif roll < 0.80:
            out[i] = ""                      # dropped glyph
        else:
            out.insert(i, rng.choice("il1|"))  # inserted stroke
    return "".join(out)


print("--- clean names ---")
check("full VIP row name", m.item_price_floor(VIP), FLOOR)
check("bare VIP name", m.item_price_floor(VIP_SHORT), FLOOR)
for name in STOCK:
    check(f"no floor for {name!r}", m.item_price_floor(name), 0)

print("\n--- the exact variants the previous matcher LOST ---")
LOST = ["IP", "UIP", "V1IP", "V:IP", "VI", "VI1P", "VI:P", "VIB", "VIF", "VIR",
        "VIb", "VIf", "VIiP", "VIlP", "VIr", "VI|P", "VP", "VTP", "ViIP",
        "VlIP", "VtP", "V|IP", "YIP", "uIP", "yIP"]
kept = sum(1 for t in LOST
           if m.item_price_floor(f"Yekaterina {t} Membership Use Period: 30 days")
           == FLOOR)
check(f"floor kept on all {len(LOST)} previously-lost variants", kept, len(LOST))

print("\n--- the known false positive ---")
check("'V|pgrade Core(High)' gets NO floor",
      m.item_price_floor("V|pgrade Core(High)"), 0)

print("\n--- fuzz: VIP must KEEP the floor ---")
rng = random.Random(20260803)
for edits, trials in ((1, 800), (2, 2000)):
    lost = []
    for _ in range(trials):
        bad = corrupt(VIP, rng, edits)
        if m.item_price_floor(bad) != FLOOR:
            lost.append(bad)
    rate = len(lost) / trials
    print(f"     {edits} edit(s): {len(lost)}/{trials} lost ({rate:.2%})")
    for sample in lost[:4]:
        print(f"       LOST {sample!r}")
    check(f"VIP keeps its floor under {edits} edit(s) (<=1%)", rate <= 0.01, True)

print("\n--- fuzz: non-VIP stock must NOT gain a floor ---")
for edits, trials in ((1, 900), (2, 2000)):
    gained = []
    for _ in range(trials):
        bad = corrupt(rng.choice(STOCK), rng, edits)
        if m.item_price_floor(bad):
            gained.append(bad)
    rate = len(gained) / trials
    print(f"     {edits} edit(s): {len(gained)}/{trials} gained ({rate:.2%})")
    for sample in gained[:4]:
        print(f"       GAINED {sample!r}")
    check(f"stock gains no floor under {edits} edit(s) (<=0.5%)", rate <= 0.005,
          True)

print(f"\nfailures: {len(fails)}")
if fails:
    raise SystemExit(1)
