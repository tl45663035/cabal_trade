import sqlite3, re, datetime, glob, os

CHAOS_COST_PER_CORE = 670_000
PACK = re.compile(r"(?:\bX\s*|(?<=[A-Za-z])X)([\d,]+)", re.I)
def pack(n):
    m = PACK.findall(n or "")
    return max(1, int(re.sub(r"[^\d]", "", m[-1]))) if m else 1
def key(n):
    return re.sub(r"[^a-z]", "", (n or "").split(" X ")[0].lower()).replace("set", "")

logs = sorted(glob.glob(r"C:/Users/Trung/Cabal/logs/run_*.log"), key=os.path.getmtime)
if not logs:
    raise SystemExit("no run logs")
newest = os.path.basename(logs[-1])
run_id = None
with open(logs[-1], encoding="utf-8", errors="ignore") as fh:
    for line in fh:
        if line.startswith("started "):
            run_id = line.split(None, 1)[1].strip()
            break
if run_id is None:
    raise SystemExit(f"{newest} has no 'started' header")
alive = os.path.getmtime(logs[-1]) > datetime.datetime.now().timestamp() - 180

c = sqlite3.connect('file:C:/Users/Trung/Cabal/sales.db?mode=ro', uri=True)
cost = {}
for i, q, s in c.execute("SELECT item, qty, spend FROM purchases WHERE at>='2026-08-19'"):
    e = cost.setdefault(key(i), [0, 0]); e[0] += q or 0; e[1] += s or 0
agg = {"Chaos": [0, 0, 0], "Cores": [0, 0, 0]}
for i, u, p in c.execute("SELECT item, qty, proceeds FROM sales WHERE run=?", (run_id,)):
    chaos = "chaos" in (i or "").lower()
    f = "Chaos" if chaos else "Cores"
    n = (u or 0) * pack(i)
    if chaos:
        cu = CHAOS_COST_PER_CORE
    else:
        cq, cs = cost.get(key(i), [0, 0]); cu = cs / cq if cq else 0
    agg[f][0] += n; agg[f][1] += p or 0; agg[f][2] += (p or 0) - cu * n
spent = c.execute("SELECT COALESCE(SUM(spend),0) FROM purchases WHERE run=?", (run_id,)).fetchone()[0]
unmeasured = c.execute("""SELECT COUNT(*) FROM sales
                          WHERE run=? AND (proceeds IS NULL OR qty IS NULL)""",
                       (run_id,)).fetchone()[0]
c.close()

ch, co = agg["Chaos"], agg["Cores"]
tu, tt, tp = ch[0]+co[0], ch[1]+co[1], ch[2]+co[2]
def m(p, t): return f"{100*p/t:.1f}%" if t else "--"
now = datetime.datetime.now().strftime("%H:%M")
state = "LIVE" if alive else "STOPPED"
print(f"THIS RUN  {run_id[11:]}  [{state}]        (as of {now})")
print(f"{'':<7}{'units':>8}{'takings':>18}{'profit':>16}{'margin':>9}")
print(f"{'-'*7}{'-'*8}{'-'*18}{'-'*16}{'-'*9}")
for lbl, v in (("Cores", co), ("Chaos", ch)):
    print(f"{lbl:<7}{v[0]:>8,}{v[1]:>18,}{v[2]:>16,.0f}{m(v[2],v[1]):>9}")
print(f"{'-'*7}{'-'*8}{'-'*18}{'-'*16}{'-'*9}")
print(f"{'TOTAL':<7}{tu:>8,}{tt:>18,}{tp:>16,.0f}{m(tp,tt):>9}")
print(f"spent this run {spent:,}")
if unmeasured:
    print(f"WARNING {unmeasured} sale(s) recorded with no quantity or proceeds "
          f"-- the figures above exclude them")
