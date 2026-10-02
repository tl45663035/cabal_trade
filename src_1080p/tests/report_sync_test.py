import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
os.environ["CABAL_CONFIG"] = "bot"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SRC.parent / "tools"))
sys.argv = ["supervise.py", "--config", "bot"]
import no_input
TRIPPED = no_input.arm()
import supervise

fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


work = Path(tempfile.mkdtemp(prefix="cabal_report_sync_"))
(work / "tools").mkdir()
marks = work / "marks.txt"
(work / "tools" / "reading.py").write_text(
    "import sys, time\n"
    "from pathlib import Path\n"
    "time.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 0.3)\n"
    f"with open({str(marks)!r}, 'a', encoding='utf-8') as out:\n"
    "    out.write(sys.argv[1] + '\\n')\n", encoding="utf-8")
(work / "tools" / "broken.py").write_text("raise SystemExit('no board yet')\n", encoding="utf-8")

events = []
supervise.ROOT = work
supervise.CONFIG = "bot"
supervise.event = lambda reason, state: events.append(reason)
supervise.K["networth_tool"] = "tools/reading.py"

began = time.perf_counter()
supervise.networth_now()
took = time.perf_counter() - began
check("a reading at report time runs to the end before the report goes on",
      marks.exists() and marks.read_text(encoding="utf-8").split() == ["bot"], marks.read_text(encoding="utf-8") if marks.exists() else "")
check("the 5-minute sampler counts it as its latest reading", time.time() - supervise._SAMPLED < 5)

marks.unlink()
background = subprocess.Popen([sys.executable, str(work / "tools" / "reading.py"), "background", "1.0"],
                              cwd=str(work), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
supervise._SAMPLING = (background, time.time())
supervise.networth_now()
check("a background reading still running is waited for, then a fresh one is taken",
      marks.read_text(encoding="utf-8").split() == ["background", "bot"], marks.read_text(encoding="utf-8"))
check("the background reading is no longer tracked", supervise._SAMPLING is None)

supervise.K["networth_tool"] = "tools/broken.py"
supervise.networth_now()
check("a failed reading is logged and does not stop the report",
      any(e.startswith("the net worth was not logged: Stop: tools/broken.py exited 1: no board yet") for e in events),
      str(events))
supervise.K["networth_tool"] = "tools/reading.py"

order = []
pushed = []
supervise.board_walked = lambda log: True
supervise.write_report = lambda: (order.append("summary"), work / "summary.txt")[1]
supervise.networth_now = lambda: order.append("net worth reading")
supervise.networth_files = lambda: (order.append("net worth files"), [work / "networth.svg"])[1]
supervise.ledger_files = lambda: (order.append("ledger files"), [])[1]
supervise.write_all = lambda: order.append("all summary")
supervise.write_all_networth = lambda: order.append("all net worth")
supervise.all_files = lambda: (order.append("all files"), [])[1]
supervise.push_report = lambda paths: (pushed.append(paths), None)[1]

supervise.CONFIG = "cang"
supervise.report_due(None, force=True)
check("a report takes its own reading after the summary and before it gathers files",
      order == ["summary", "net worth reading", "net worth files", "ledger files"], str(order))
check("and pushes the summary with the graph", pushed and pushed[-1] == [work / "summary.txt", work / "networth.svg"],
      str(pushed))

order.clear()
supervise.CONFIG = "bot"
supervise.report_due(None, force=True)
check("on the all machine the reading also comes before the combined net worth",
      order.index("net worth reading") < order.index("all net worth"), str(order))

order.clear()
supervise.write_report = lambda: (_ for _ in ()).throw(RuntimeError("no summary"))
supervise.report_due(None, force=True)
check("no summary means no reading and no push", order == [] and len(pushed) == 2, str(order))
check("no input reached the game", not TRIPPED, str(TRIPPED))
print(f"a reading at report time with a 0.3 s tool took {took * 1000:.0f} ms in all")
print(f"{fails} failure(s)")
sys.exit(1 if fails else 0)
