import sys, os, time, functools
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import calibration, row_model, driver

if len(sys.argv) < 3:
    sys.exit("usage: py tools/hover_test.py ROW HOVER_SECONDS [HOVER_SECONDS ...]\n"
             "  cancels ROW once per HOVER value, timing the Change click and\n"
             "  reporting whether the dialog answered. Each run cancels a real listing.")

row = int(sys.argv[1])
hovers = [float(x) for x in sys.argv[2:]]
print(f"row {row}, hover values {hovers}")
print("each run CANCELS A REAL LISTING and returns it to inventory\n")

for hover in hovers:
    row_model.ACTION_GAP = hover
    marks = {}
    real_find = row_model.find_button
    @functools.wraps(real_find)
    def timed(word, *a, **k):
        t = time.perf_counter()
        out = real_find(word, *a, **k)
        marks[word] = ((time.perf_counter() - t) * 1000, out is not None)
        return out
    row_model.find_button = timed
    started = time.perf_counter()
    try:
        driver.do_cancel(row, verbose=False)
        ok = True
    except Exception as exc:
        ok = False
        print(f"  hover {hover:.2f}s -> FAILED: {type(exc).__name__}: {exc}")
    finally:
        row_model.find_button = real_find
    if ok:
        total = (time.perf_counter() - started) * 1000
        bits = "  ".join(f"{w} {ms:.0f}ms{'' if found else ' NOT FOUND'}"
                         for w, (ms, found) in marks.items())
        print(f"  hover {hover:.2f}s -> total {total:.0f}ms   {bits}")
