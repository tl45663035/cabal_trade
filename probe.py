import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))
import calibration as C
import open_inventory as inv

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

rule("bringing the game forward and opening the Inventory")
import time

try:
    say("focus_game    :", inv.focus_game())
except Exception as exc:
    say("focus_game    : FAILED", type(exc).__name__, exc)
time.sleep(C.load_shared()["timing"]["focus_settle"])

before = C.grab()
before.save(os.path.join(OUT, "screen_before_I.png"))
say("saved screen_before_I.png")

def moved(a, b):
    from PIL import ImageChops
    spread = ImageChops.difference(a.convert("L"), b.convert("L"))
    return sum(spread.histogram()[13:])


say("")
say("pressing I until the screen moves. It stops as soon as something")
say("changes, so the panel is never toggled shut by a second press.")
image = before
opened = False
for attempt in (1, 2, 3):
    was = image
    try:
        inv.press(inv.VK_I)
        say(f"  press {attempt}: the keystroke was sent")
    except Exception as exc:
        say(f"  press {attempt}: FAILED {type(exc).__name__}: {exc}")
        break
    time.sleep(C.load_shared()["timing"]["tab_settle"])
    image = C.grab()
    image.save(os.path.join(OUT, f"screen_after_I_{attempt}.png"))
    shift = moved(was, image)
    say(f"     {shift:,} pixels changed")
    if shift > 1000:
        say(f"     the screen moved, so the keystroke reached the game")
        opened = True
        break
    say(f"     nothing moved; the game did not take it")
    try:
        say(f"     foreground is the game: "
            f"{inv._user32.GetForegroundWindow() == inv.find_game_window()}")
    except Exception as exc:
        say(f"     (could not check the foreground: {exc})")
    inv.focus_game()
    time.sleep(C.load_shared()["timing"]["focus_settle"])

image.save(os.path.join(OUT, "screen_after_I.png"))
say("saved screen_after_I.png")
if not opened:
    say("  the game never responded to I.")
say("")
say("LOOK AT screen_after_I.png BEFORE READING ON. Everything below assumes a")
say("character standing in the world with the Inventory panel open. A login")
say("screen, a character select, or a loading screen explains every failure")
say("after this point and none of them are worth investigating.")

say("")
band = C._box(C.ALZ_SEARCH_F)
say("balance in the configured band:", C.find_alz(image, band))
w2, h2 = image.size
hit = C.find_alz(image, (0, 0, w2, h2))
say("balance anywhere on screen    :", hit)
if hit:
    wide = (hit[0] - 4, hit[1] - 6, hit[2] + 60, hit[3] + 6)
    say("  reads                       :", repr(C.read_line(image, wide)))
    keep(image, wide, "balance_found")
    x, y, cw, ch = C._client_rect()
    say("  put this in regions.alz_search: [%.4f, %.4f, %.4f, %.4f]"
        % ((hit[0] - x) / cw, (hit[1] - y) / ch,
           (hit[2] - x) / cw, (hit[3] - y) / ch))
else:
    say("  nothing gold and digit-shaped anywhere. Counting how close it got:")
    px = image.convert("RGB").load()
    gold = 0
    for yy in range(0, h2, 2):
        for xx in range(w2 // 2, w2, 2):
            r, g, b = px[xx, yy]
            hi, lo = max(r, g, b), min(r, g, b)
            if hi > C.ALZ_BRIGHT and hi - lo > C.ALZ_SATURATION:
                gold += 1
    say(f"    gold-ish pixels in the right half (sampled 1 in 4): {gold:,}")
    say(f"    ALZ_MIN_PIXELS is {C.ALZ_MIN_PIXELS}, ALZ_MIN_HEIGHT "
        f"{C.ALZ_MIN_HEIGHT}, ALZ_MAX_HEIGHT {C.ALZ_MAX_HEIGHT}")

rule("hunting the balance without find_alz's rules")
say("find_alz keeps every gold pixel near the busiest row and gives up if")
say("what it keeps spans most of the width, which the game world does. This")
say("looks for small runs of gold that could be a number instead.")
say("")

px = image.convert("RGB").load()
w3, h3 = image.size
rows = {}
for yy in range(h3):
    run = []
    for xx in range(w3):
        r, g, b = px[xx, yy]
        hi, lo = max(r, g, b), min(r, g, b)
        if hi > C.ALZ_BRIGHT and hi - lo > C.ALZ_SATURATION:
            run.append(xx)
    if run:
        rows[yy] = run

blobs = []
for yy, run in rows.items():
    start = prev = run[0]
    for xx in run[1:] + [None]:
        if xx is not None and xx - prev <= 6:
            prev = xx
            continue
        if 30 <= prev - start <= 420:
            blobs.append((yy, start, prev))
        if xx is None:
            break
        start = prev = xx

merged = []
for yy, x0, x1 in sorted(blobs):
    for got in merged:
        if abs(got[0] - yy) <= 16 and not (x1 < got[1] - 20 or x0 > got[2] + 20):
            got[0] = yy
            got[1] = min(got[1], x0)
            got[2] = max(got[2], x1)
            got[3] = min(got[3], yy)
            got[4] = max(got[4], yy)
            break
    else:
        merged.append([yy, x0, x1, yy, yy])

say(f"{len(merged)} run(s) of gold that could be a number; reading each:")
money = []
for _y, x0, x1, top, bot in merged:
    if not (C.ALZ_MIN_HEIGHT <= bot - top + 1 <= C.ALZ_MAX_HEIGHT):
        continue
    box = (x0 - 4, top - 5, x1 + 46, bot + 5)
    text = C.read_line(image, box)
    value = C._digits(text or "")
    if value and value > 999:
        money.append((value, box, text))

money.sort(key=lambda m: -m[0])
for value, box, text in money[:12]:
    x, y, cw, ch = C._client_rect()
    say(f"  {value:>14,}  at {box}  {text!r}")
    say(f"      fractions [%.4f, %.4f, %.4f, %.4f]"
        % ((box[0] - x) / cw, (box[1] - y) / ch,
           (box[2] - x) / cw, (box[3] - y) / ch))
if money:
    best = money[0]
    keep(image, best[1], "balance_candidate")
    say("")
    say("The largest is most likely the balance. Its crop is saved as")
    say("balance_candidate.png -- check it before trusting it.")
else:
    say("  nothing that reads as a number over 999.")

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
    ("sweep the screen for the balance", lambda: C.locate_alz(verbose=True)),
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
