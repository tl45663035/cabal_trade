import sqlite3, re, datetime
S = "2026-08-20T08:00:00"
PACK = re.compile(r"(?:\bX\s*|(?<=[A-Za-z])X)([\d,]+)", re.I)
def pack(n):
    m = PACK.findall(n or "")
    return max(1, int(re.sub(r"[^\d]", "", m[-1]))) if m else 1
def key(n):
    return re.sub(r"[^a-z]", "", (n or "").split(" X ")[0].lower()).replace("set", "")
c = sqlite3.connect('file:C:/Users/Trung/Cabal/sales.db?mode=ro', uri=True)
cost = {}
for i, q, s in c.execute("SELECT item, qty, spend FROM purchases WHERE at>='2026-08-19'"):
    e = cost.setdefault(key(i), [0, 0]); e[0] += q or 0; e[1] += s or 0
agg = {"Chaos": [0, 0, 0], "Cores": [0, 0, 0]}
for i, u, p in c.execute("SELECT item, qty, proceeds FROM sales WHERE at>=?", (S,)):
    f = "Chaos" if "chaos" in (i or "").lower() else "Cores"
    n = (u or 0) * pack(i)
    cq, cs = cost.get(key(i), [0, 0]); cu = cs / cq if cq else 0
    agg[f][0] += n; agg[f][1] += p or 0; agg[f][2] += (p or 0) - cu * n
c.close()
ch, co = agg["Chaos"], agg["Cores"]
tu, tt, tp = ch[0]+co[0], ch[1]+co[1], ch[2]+co[2]
def m(p, t): return f"{100*p/t:.1f}%" if t else "--"
now = datetime.datetime.now().strftime("%H:%M")
print(f"PROFIT SINCE 08:00          (as of {now})")
print(f"{'':<7}{'units':>8}{'takings':>18}{'profit':>16}{'margin':>9}")
print(f"{'-'*7}{'-'*8}{'-'*18}{'-'*16}{'-'*9}")
for lbl, v in (("Cores", co), ("Chaos", ch)):
    print(f"{lbl:<7}{v[0]:>8,}{v[1]:>18,}{v[2]:>16,.0f}{m(v[2],v[1]):>9}")
print(f"{'-'*7}{'-'*8}{'-'*18}{'-'*16}{'-'*9}")
print(f"{'TOTAL':<7}{tu:>8,}{tt:>18,}{tp:>16,.0f}{m(tp,tt):>9}")
