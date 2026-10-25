import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "cang"
os.chdir(SRC)
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SRC))
sys.argv = ["dungeon.py", "--config", "cang"]
import no_input
TRIPPED = no_input.arm()
import dungeon
from PIL import Image

fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


with Image.open(HERE / "fixtures" / "legendary_box_target.png") as frame:
    image = frame.convert("RGB")
bars = dungeon.target_bars(image)
check("the Legendary Box bar reads as targeted, 10 of 10 HP, on a real frame where "
      "OCR turns '10 / 10' into '1of10'",
      [(dungeon.hp_left(bar), dungeon.hp_most(bar)) for bar in bars] == [(10, 10)],
      repr(bars))

for raw, want in [("LeaendanyBox1of10", "LeaendanyBox10/10"),
                  ("LegendaryBox9f10", "LegendaryBox9/10"),
                  ("DivineGolem ofRaae 1929296/1929296", "DivineGolem ofRaae 1929296/1929296"),
                  ("DiyineGolemofRaae0/1929296", "DiyineGolemofRaae0/1929296"),
                  ("IllusiyeVarrior99926/324755", "IllusiyeVarrior99926/324755")]:
    got = dungeon.slash_misread(raw)
    check(f"{raw!r} reads as {want!r}", got == want, repr(got))

check("no input reached the game", not TRIPPED, str(TRIPPED))
print(f"{fails} failure(s)")
sys.exit(1 if fails else 0)
