import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))
import calibration as C

OUT = os.path.join(HERE, "probe_out")
os.makedirs(OUT, exist_ok=True)
REPORT = open(os.path.join(OUT, "probe.txt"), "w", encoding="utf-8")


def say(*bits):
    line = " ".join(str(b) for b in bits)
    print(line)
    REPORT.write(line + "\n")
    REPORT.flush()


def rule(title):
    say("")
    say("=" * 72)
    say(title)
    say("=" * 72)


def keep(image, box, name):
    try:
        x0, y0, x1, y1 = (int(v) for v in box)
        pad = 8
        crop = image.crop((max(0, x0 - pad), max(0, y0 - pad),
                           min(image.size[0], x1 + pad),
                           min(image.size[1], y1 + pad)))
        if crop.size[0] and crop.size[1]:
            crop.save(os.path.join(OUT, f"{name}.png"))
    except Exception as exc:
        say(f"    (could not save {name}: {type(exc).__name__}: {exc})")


C.frames_on(True)

rule("the screen")
say("Open the Inventory in game before running this -- the balance only")
say("shows while that panel is up. This probe will press I itself if it")
say("cannot see the balance, so expect the keyboard to be used.")
say("")
image = C.grab()
say("screen size    :", image.size)
say("client rect    :", C._client_rect())
say("resolution key :", C.resolution_key())
image.save(os.path.join(OUT, "screen.png"))
say("saved screen.png")

try:
    win = C.find_game_window()
    say("game window    :", win)
except Exception as exc:
    say("game window    : FAILED", type(exc).__name__, exc)

rule("what calibration.json already holds")
try:
    data = json.loads(C.OUT.read_text(encoding="utf-8"))
    say("resolutions    :", sorted(data.get("by_resolution") or {}))
    say("top-level keys :", sorted(data))
except Exception as exc:
    say("could not read calibration.json:", type(exc).__name__, exc)

rule("the balance")
band = C._box(C.ALZ_SEARCH_F)
say("alz_search frac:", list(C.ALZ_SEARCH_F))
say("            box:", band)
keep(image, band, "alz_search_band")
say("find_alz in it :", C.find_alz(image, band))

w, h = image.size
for name, area in (("right half", (w // 2, 0, w, h)),
                   ("bottom right", (w // 2, h // 2, w, h)),
                   ("whole screen", (0, 0, w, h))):
    try:
        hit = C.find_alz(image, area)
    except Exception as exc:
        say(f"sweep {name:<13}: FAILED {type(exc).__name__}: {exc}")
        continue
    say(f"sweep {name:<13}: {hit}")
    if hit:
        wide = (hit[0] - 4, hit[1] - 6, hit[2] + 60, hit[3] + 6)
        say("   reads       :", repr(C.read_line(image, wide)))
        say("   read_money  :", C.read_money(image, wide))
        keep(image, wide, f"balance_{name.replace(' ', '_')}")
        x, y, cw, ch = C._client_rect()
        say("   as fractions: [%.4f, %.4f, %.4f, %.4f]"
            % ((hit[0] - x) / cw, (hit[1] - y) / ch,
               (hit[2] - x) / cw, (hit[3] - y) / ch))
        break

rule("the panels the code looks for")
for probe in ("inventory_open", "_trade_window_open", "vendor_open",
              "craft_window_open", "purchase_tab_showing"):
    try:
        fn = getattr(C, probe)
        say(f"{probe:22}: {fn(image) if probe == 'inventory_open' else fn()}")
    except Exception as exc:
        say(f"{probe:22}: FAILED {type(exc).__name__}: {exc}")

rule("every region, where it lands and what it reads")
for name in sorted(C._REG):
    frac = C._REG[name]
    if not (isinstance(frac, (list, tuple)) and len(frac) == 4):
        say(f"{name:26} not a box, it is {frac!r}")
        continue
    try:
        box = C._box(tuple(frac))
    except Exception as exc:
        say(f"{name:26} FAILED {type(exc).__name__}: {exc}")
        continue
    keep(image, box, f"region_{name}")
    try:
        words = [t for t, _c, _p in C.ocr(image, box)]
    except Exception as exc:
        words = [f"<{type(exc).__name__}: {exc}>"]
    say(f"{name:26} {str(box):26} {words[:10]}")

rule("walking the calibration one step at a time")
say("each step is tried on its own so the first that fails is plain to see.")
say("")

steps = (
    ("find the game window", lambda: C.find_game_window()),
    ("inventory panel open", lambda: C.inventory_open()),
    ("await the inventory", lambda: C.await_inventory(verbose=True)),
    ("measure the inventory", lambda: C.calibrate_inventory(verbose=True)),
)
for title, run in steps:
    say("")
    say("-" * 60)
    say(title)
    say("-" * 60)
    try:
        got = run()
        say("  ->", json.dumps(got, indent=2, default=str)
            if isinstance(got, dict) else got)
    except Exception as exc:
        say("  FAILED", type(exc).__name__ + ":", exc)
        say(traceback.format_exc())
        break

after = C.grab()
after.save(os.path.join(OUT, "screen_after.png"))
say("")
say("saved screen_after.png")

rule("done")
say("everything is in", OUT)
REPORT.close()
