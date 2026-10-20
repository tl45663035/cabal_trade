import contextlib
import importlib.util
import io
import os
import queue
import statistics
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
os.chdir(SRC)
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.argv = ["supervise.py", "--config", "bot", "--plan"]
import no_input
TRIPPED = no_input.arm()
import calibration

fails = 0
floor = calibration.MIN_FREE_MB


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


def board(names):
    root = Path(tempfile.mkdtemp(prefix="cabal_frames_"))
    for name in names:
        (root / name).mkdir()
        (root / name / "00001_click.png").write_bytes(b"x")
    return root


sizes = {}
base = {"mb": 0}


def free_at(mb):
    base["mb"] += mb - calibration.free_mb()


saved = (calibration.FRAME_DIR, calibration.RUN_FRAMES, calibration.free_mb, calibration._NOTHING_TO_CLEAR,
         calibration._TRIMMING)
calibration.free_mb = lambda: base["mb"] + sum(mb for name, mb in sizes.items()
                                               if not (calibration.FRAME_DIR / name).exists())
try:
    names = ["2026-09-28_193147_run", "2026-09-28_213944_recovery", "2026-09-28_214324_run", "2026-09-28_223831_run"]
    calibration.FRAME_DIR = board(names)
    calibration.RUN_FRAMES = calibration.FRAME_DIR / names[-1]
    sizes.update({names[0]: 3000, names[1]: 30, names[2]: 1800, names[3]: 900})

    base["mb"] = floor + 10
    with contextlib.redirect_stdout(io.StringIO()):
        calibration.make_room()
    check(f"with {floor:,} megabytes or more free nothing is cleared",
          all((calibration.FRAME_DIR / n).exists() for n in names))

    base["mb"] = floor - 3020
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        calibration.make_room()
    left = sorted(p.name for p in calibration.FRAME_DIR.iterdir())
    check("under the floor the earliest runs' frames are cleared, oldest first, until the floor is met",
          left == names[2:], f"left {left}")
    check("and it says what it cleared", "cleared the debug frames of 2 earlier run(s)" in said.getvalue(),
          said.getvalue().strip())
    check("the run's own frames stay while clearing earlier runs is enough",
          (calibration.RUN_FRAMES / "00001_click.png").exists())

    shots = [calibration.RUN_FRAMES / f"{n:05d}_click.png" for n in range(1, 11)]
    for age, shot in enumerate(shots):
        shot.write_bytes(b"x")
        os.utime(shot, (age, age))
    frame_mb = 100
    by_folder = calibration.free_mb
    calibration.free_mb = lambda: by_folder() + frame_mb * sum(not shot.exists() for shot in shots)

    free_at(floor - sizes[names[2]] - 350)
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        calibration.make_room()
    left = sorted(p.name for p in calibration.FRAME_DIR.iterdir())
    kept = [shot.exists() for shot in shots]
    check("with no earlier run left, the run's own oldest frames are cleared, oldest first, until the floor is met",
          left == [names[-1]] and kept == [False] * 4 + [True] * 6, f"left {left}, kept {kept}")
    check("and it says what it cleared", "cleared this run's 4 oldest frames" in said.getvalue(),
          said.getvalue().strip())

    free_at(floor - 200)
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        calibration.make_room()
    kept = [shot.exists() for shot in shots]
    check("each later call under the floor clears the next oldest ones", kept == [False] * 6 + [True] * 4,
          f"kept {kept}")
    check("without saying it again", said.getvalue() == "", said.getvalue().strip())

    free_at(floor - 2000)
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        calibration.make_room()
        calibration.make_room()
    check("when no frame is left and the disk is still under the floor it says so once, not every time",
          not any(shot.exists() for shot in shots) and said.getvalue().count("are left to clear") == 1,
          said.getvalue().strip())

    free_at(floor + frame_mb // 2)
    with contextlib.redirect_stdout(io.StringIO()):
        calibration.make_room()
    shots[0].write_bytes(b"x")
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        calibration.make_room()
    check("once the disk was back over the floor, the next clearing of the run's frames is said again",
          not shots[0].exists() and "cleared this run's 1 oldest frames" in said.getvalue(),
          said.getvalue().strip())
finally:
    (calibration.FRAME_DIR, calibration.RUN_FRAMES, calibration.free_mb, calibration._NOTHING_TO_CLEAR,
     calibration._TRIMMING) = saved

calls = []
saved = (calibration.make_room, calibration.RUN_FRAMES, calibration.FRAME_DIR, calibration.FRAMES_ON,
         calibration.prune_frames, calibration._FRAME_QUEUE)
calibration.make_room = lambda: calls.append(("room", calibration.RUN_FRAMES))
calibration.prune_frames = lambda: calls.append(("prune", None))
calibration.FRAME_DIR = Path(tempfile.mkdtemp(prefix="cabal_start_"))
calibration.RUN_FRAMES = None
try:
    with contextlib.redirect_stdout(io.StringIO()):
        calibration.frames_on(True)
    check("a run clears room when it starts, once its own folder is known",
          calls and calls[0][0] == "room" and calls[0][1] is not None and calls[0][1].parent == calibration.FRAME_DIR,
          str(calls))
    calls.clear()
    calibration._FRAME_QUEUE = queue.Queue()
    calibration._FRAME_QUEUE.put(("prune",))
    calibration._FRAME_QUEUE.put(None)
    calibration._scribe()
    check("and again at every frame-budget prune while it runs", [c[0] for c in calls] == ["prune", "room"], str(calls))
finally:
    (calibration.make_room, calibration.RUN_FRAMES, calibration.FRAME_DIR, calibration.FRAMES_ON,
     calibration.prune_frames, calibration._FRAME_QUEUE) = saved
    calibration.frames_on(False)

took = []
for _ in range(100):
    began = time.perf_counter()
    calibration.free_mb()
    took.append((time.perf_counter() - began) * 1000)
check("reading the free space costs almost nothing", statistics.median(took) < 1.0,
      f"median {statistics.median(took):.3f} ms")

spec = importlib.util.spec_from_file_location("supervise", SRC.parent / "tools" / "supervise.py")
sup = importlib.util.module_from_spec(spec)
with contextlib.redirect_stdout(io.StringIO()):
    spec.loader.exec_module(sup)
dead = Path(tempfile.mkdtemp(prefix="cabal_dead_"))
frames = Path(tempfile.mkdtemp(prefix="cabal_frames_"))
names = [f"2026-09-2{d}_{h:02d}0000_run" for d in (7, 8) for h in (1, 2, 3, 4)]
for name in names[:-1]:
    (dead / name).mkdir()
    (dead / name / "00001_click.png").write_bytes(b"x")
run = frames / names[-1]
run.mkdir()
for n in range(3):
    (run / f"{n:05d}_click.png").write_bytes(b"x")
saved = (sup.DEAD, calibration.FRAME_DIR, calibration.VIDEO_DIR)
sup.DEAD, calibration.FRAME_DIR, calibration.VIDEO_DIR = dead, frames, frames / "no_video"
try:
    said = io.StringIO()
    with contextlib.redirect_stdout(said):
        sup.keep_evidence(Path(names[-1] + ".log"))
    left = sorted(p.name for p in dead.iterdir())
    check(f"keeping a dead run clears the older ones down to the newest {sup.K['keep_dead_runs']}",
          left == names[-sup.K["keep_dead_runs"]:], f"{left}")
    check("and the newest dead run's frames are kept", len(list((dead / names[-1]).glob("*.png"))) == 3,
          said.getvalue().strip()[:160])
finally:
    sup.DEAD, calibration.FRAME_DIR, calibration.VIDEO_DIR = saved

missing = Path(tempfile.mkdtemp(prefix="cabal_none_")) / "dead_runs"
saved = sup.DEAD
sup.DEAD = missing
try:
    sup.trim_dead_runs()
    check("with no dead-run folder yet, trimming does nothing", not missing.exists())
finally:
    sup.DEAD = saved

check("no real input reached the game during the whole test", TRIPPED == [], f"{TRIPPED}")
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
