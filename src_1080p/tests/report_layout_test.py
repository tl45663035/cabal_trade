import contextlib
import datetime
import io
import os
import sqlite3
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
import networth_all
import networth_log
import ledger_export

fails = 0


def check(label, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   [{detail}]" if detail else ""))


def git(*args, cwd):
    out = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if out.returncode:
        raise RuntimeError(out.stderr)
    return out.stdout


def lay(root, files):
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")


def folder(tree, name):
    return {t for t in tree if t.startswith(f"profit_reports/{name}/")}


def keys(files, name):
    return {k for k in files if k.startswith(f"profit_reports/{name}/")}


work = Path(tempfile.mkdtemp(prefix="cabal_report_layout_"))
origin, seed, clone = work / "origin.git", work / "seed", work / "clone"
git("init", "-q", "--bare", "-b", "main", str(origin), cwd=work)
git("init", "-q", "-b", "main", str(seed), cwd=work)
OLD = {
    "profit_reports/bot/summary.txt": "bot summary old\n",
    "profit_reports/bot/networth.txt": "bot networth old\n",
    "profit_reports/bot/networth.svg": "<svg>bot old</svg>\n",
    "profit_reports/bot/networth_history.csv": "bot history old\n",
    "profit_reports/bot/ledger/sales.csv": "id\n",
    "profit_reports/bot/ledger/purchases.csv": "id\n",
    "profit_reports/bot/ledger/board.csv": "row\n",
    "profit_reports/all/summary.txt": "all summary old\n",
    "profit_reports/all/networth.txt": "all networth old\n",
    "profit_reports/all/networth.svg": "<svg>all old</svg>\n",
    "profit_reports/cang/summary.txt": "cang summary old\n",
    "profit_reports/cang/networth.txt": "cang networth old layout\n",
    "profit_reports/cang/networth.svg": "<svg>cang old</svg>\n",
    "profit_reports/cang/networth_history.csv": "cang history old layout\n",
    "tools/keep.py": "kept = 1\n",
}
lay(seed, OLD)
git("add", "-A", cwd=seed)
git("-c", "user.name=test", "-c", "user.email=test@test", "commit", "-q", "-m", "old layout", cwd=seed)
git("push", "-q", str(origin), "main", cwd=seed)
git("clone", "-q", str(origin), str(clone), cwd=work)
git("config", "user.name", "test", cwd=clone)
git("config", "user.email", "test@test", cwd=clone)
(clone / "tools" / "fail.py").write_text("raise SystemExit(3)\n", encoding="utf-8")

NEW = {
    "profit_reports/bot/summary.txt": "bot summary new\n",
    "profit_reports/bot/networth.svg": "<svg>bot new</svg>\n",
    "profit_reports/bot/artifacts/networth.txt": "bot networth new\n",
    "profit_reports/bot/artifacts/networth_history.csv": "bot history new\n",
    "profit_reports/bot/artifacts/sales.csv": "id\n1\n",
    "profit_reports/bot/artifacts/purchases.csv": "id\n1\n",
    "profit_reports/bot/artifacts/board.csv": "row\n1\n",
    "profit_reports/all/summary.txt": "all summary new\n",
    "profit_reports/all/networth.svg": "<svg>all new</svg>\n",
    "profit_reports/all/artifacts/networth.txt": "all networth new\n",
}
lay(clone, NEW)

events = []
supervise.ROOT = clone
supervise.LOGS = work / "logs"
supervise.LOGS.mkdir()
supervise.CONFIG = "bot"
supervise.report_now = lambda: "12:00:00"
supervise.event = lambda reason, state: events.append(reason)
supervise.K["ledger_tool"] = "tools/fail.py"

ledger = supervise.ledger_files()
check("a failed export still hands over the last ledger files from artifacts",
      {p.relative_to(clone).as_posix() for p in ledger}
      == {"profit_reports/bot/artifacts/sales.csv", "profit_reports/bot/artifacts/purchases.csv",
          "profit_reports/bot/artifacts/board.csv"}, str(ledger))
check("and says the ledger was not exported", any("the ledger was not exported" in e for e in events), str(events))
paths = [supervise.report_path()] + supervise.networth_files() + ledger + supervise.all_files()
check("the push lists the summary and graph at the top and the rest in artifacts",
      {p.relative_to(clone).as_posix() for p in paths} == set(NEW),
      str(sorted(p.relative_to(clone).as_posix() for p in paths)))

started = time.perf_counter()
failed = supervise._push_report(paths)
took = time.perf_counter() - started
check("bot's report push goes through", failed is None, str(failed))
tree = set(git("ls-tree", "-r", "--name-only", "main", cwd=origin).split())
check("bot's folder on origin holds exactly the new layout, the old copies gone although still on disk",
      folder(tree, "bot") == keys(NEW, "bot"), str(sorted(folder(tree, "bot"))))
check("all's folder on origin holds exactly the new layout",
      folder(tree, "all") == keys(NEW, "all"), str(sorted(folder(tree, "all"))))
check("cang's folder is left alone by bot", folder(tree, "cang") == keys(OLD, "cang"),
      str(sorted(folder(tree, "cang"))))
check("files outside the report folders are left alone", "tools/keep.py" in tree)
check("the pushed artifacts carry the files' contents",
      git("show", "main:profit_reports/bot/artifacts/networth.txt", cwd=origin) == "bot networth new\n")
check("the commit is a report commit on top of origin",
      git("log", "-1", "--format=%s", "main", cwd=origin).strip() == "profit report for bot at 12:00:00")
check("nothing new to push is no change", supervise._push_report(paths) == "no change")

networth_all.ROOT = clone
networth_all.FOLDER = clone / "profit_reports"
SUP = networth_all.SUP
check("the combined report reads cang's old file while cang is on the old layout",
      networth_all.page("cang", "bot") == "cang networth old layout\n")
check("and cang's old history file",
      networth_all.page("cang", "bot", SUP["networth_history_report"]) == "cang history old layout\n")
check("and this machine's own artifacts file",
      networth_all.page("bot", "bot") == "bot networth new\n")

CANG = {
    "profit_reports/cang/summary.txt": "cang summary new\n",
    "profit_reports/cang/networth.svg": "<svg>cang new</svg>\n",
    "profit_reports/cang/artifacts/networth.txt": "cang networth new\n",
    "profit_reports/cang/artifacts/networth_history.csv": "cang history new\n",
}
lay(clone, CANG)
supervise.CONFIG = "cang"
cang_paths = [supervise.report_path()] + supervise.networth_files()
check("cang's push goes through", supervise._push_report(cang_paths) is None)
tree = set(git("ls-tree", "-r", "--name-only", "main", cwd=origin).split())
check("cang's folder on origin holds exactly its new layout", folder(tree, "cang") == keys(CANG, "cang"),
      str(sorted(folder(tree, "cang"))))
check("cang leaves bot's and all's folders alone",
      folder(tree, "bot") == keys(NEW, "bot") and folder(tree, "all") == keys(NEW, "all"))
check("the combined report reads cang's artifacts once cang has moved",
      networth_all.page("cang", "bot") == "cang networth new\n")
check("and cang's history from artifacts",
      networth_all.page("cang", "bot", SUP["networth_history_report"]) == "cang history new\n")

index = work / "timing_index"
env = dict(os.environ, GIT_INDEX_FILE=str(index))
subprocess.run(["git", "read-tree", "origin/main"], cwd=str(clone), env=env, check=True)
began = time.perf_counter()
subprocess.run(["git", "rm", "--cached", "-r", "-f", "-q", "--ignore-unmatch", "--",
                "profit_reports/bot", "profit_reports/all"], cwd=str(clone), env=env, check=True)
cleared_ms = (time.perf_counter() - began) * 1000

networth_log.LOGS = work / "logs"
networth_log.REPORTS = work / "reports"
networth_log.reading = lambda: (datetime.datetime(2026, 10, 1, 12, 0, 0), 100, 200, 30, 4)
sys.argv = ["networth_log.py", "bot"]
with contextlib.redirect_stdout(io.StringIO()):
    networth_log.main()
written = {p.relative_to(work).as_posix() for p in work.rglob("*") if p.is_file()
           and p.relative_to(work).parts[0] in ("reports", "logs")}
check("the net worth tool writes its table and history into artifacts and the graph at the top",
      {"reports/bot/networth.svg", "reports/bot/artifacts/networth.txt",
       "reports/bot/artifacts/networth_history.csv", "logs/networth_history_bot.csv"} <= written,
      str(sorted(written)))
svg = (work / "reports" / "bot" / "networth.svg").read_text(encoding="utf-8")
check("the graph is titled with the config, not the folder", "BOT " in svg and "ARTIFACTS" not in svg)
check("the history it keeps and the one it pushes match",
      (work / "logs" / "networth_history_bot.csv").read_text(encoding="utf-8")
      == (work / "reports" / "bot" / "artifacts" / "networth_history.csv").read_text(encoding="utf-8"))

db = work / "sales.db"
con = sqlite3.connect(db)
for table in ("sales", "purchases", "board"):
    con.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, item TEXT)")
    con.execute(f"INSERT INTO {table} (item) VALUES ('Chaos Core')")
con.commit()
con.close()
ledger_export.ROOT = work / "ledger_root"
ledger_export.LEDGER = db
sys.argv = ["ledger_export.py", "bot"]
printed = io.StringIO()
with contextlib.redirect_stdout(printed):
    ledger_export.main()
check("the ledger export writes straight into artifacts",
      printed.getvalue().split() == ["profit_reports/bot/artifacts/sales.csv",
                                     "profit_reports/bot/artifacts/purchases.csv",
                                     "profit_reports/bot/artifacts/board.csv"], printed.getvalue())
check("no input reached the game", not TRIPPED, str(TRIPPED))
print(f"bot's whole report push took {took * 1000:.0f} ms; clearing the two folders from the index took {cleared_ms:.0f} ms")
print(f"{fails} failure(s)")
sys.exit(1 if fails else 0)
