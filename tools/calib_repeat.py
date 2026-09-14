import copy
import functools
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src_1080p"))
import calibration

KNOBS = calibration.load_shared()["tools"]
N = int(sys.argv[1]) if len(sys.argv) > 1 else int(KNOBS["calib_repeats"])
OUT = sys.argv[2]
FAIL_LIMIT = int(KNOBS["calib_fail_limit"])

MARKS = []


def wrap(name):
    fn = getattr(calibration, name)

    @functools.wraps(fn)
    def inner(*a, **k):
        t = time.perf_counter()
        try:
            return fn(*a, **k)
        finally:
            MARKS.append((name, a[:2], (time.perf_counter() - t) * 1000,
                          time.perf_counter()))
    setattr(calibration, name, inner)


for n in ("click", "ctrl_click", "calibrate_actions", "calibrate_inventory",
          "calibrate_shop", "calibrate_purchase", "calibrate_register_table",
          "calibrate_panel", "close_everything"):
    wrap(n)

runs = []
fails = 0
for i in range(1, N + 1):
    MARKS.clear()
    started = time.perf_counter()
    err = ""
    try:
        calibration.main(close=True)
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
    total = (time.perf_counter() - started) * 1000
    phases = {n: ms for n, _a, ms, _t in MARKS
              if n.startswith("calibrate_") or n == "close_everything"}
    ctrl = [t for n, _a, _ms, t in MARKS if n == "ctrl_click"]
    cancel_ms = list_ms = None
    end_marks = [t for n, _a, _ms, t in MARKS if n == "calibrate_actions"]
    if end_marks:
        act_total = phases.get("calibrate_actions")
        if ctrl:
            act_end = end_marks[-1]
            list_ms = (act_end - ctrl[0]) * 1000
            cancel_ms = act_total - list_ms if act_total else None
    snap = {}
    try:
        data = json.loads(calibration.OUT.read_text(encoding="utf-8"))
        per = data["by_resolution"][calibration.resolution_key()]
        snap = {"shop": copy.deepcopy(per["shop"]),
                "inventory": copy.deepcopy(per["inventory"])}
    except Exception as exc:
        snap = {"error": str(exc)}
    runs.append({"i": i, "ms": total, "err": err, "phases": phases,
                 "cancel_ms": cancel_ms, "list_ms": list_ms, "snap": snap})
    fails = fails + 1 if err else 0
    print(f"{i}/{N}  {total:7.0f} ms  {'FAIL ' + err if err else 'ok'}",
          flush=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(runs, fh)
    if fails >= FAIL_LIMIT:
        print(f"{FAIL_LIMIT} failures in a row - stopping", flush=True)
        break
