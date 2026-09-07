import collections
import datetime
import pathlib
import re
import sqlite3

ROOT = pathlib.Path(__file__).resolve().parent.parent
LEDGER = ROOT / "src_1080p" / "sales.db"
LOGS = ROOT / "src_1080p" / "logs"
PACK = re.compile(r"\bX\s*[\d,]+", re.I)
DAYS_BACK = 7
ENDED = re.compile(r"ended (\d\d):(\d\d):(\d\d), ran for")
LOG_STAMP = "%Y-%m-%d_%H%M%S"


def key(name):
    stripped = PACK.sub(" ", name or "")
    return re.sub(r"[^a-z]", "", stripped.lower()).replace("set", "")


def pack(name):
    found = PACK.findall(name or "")
    if not found:
        return 1
    return max(1, int(re.sub(r"[^\d]", "", found[-1])))


def units_sold(item, qty):
    per = pack(item)
    if per == 1 or not qty:
        return qty
    return max(1, round(qty / per)) * per


def bucket(name):
    return "Chaos" if "chaos" in (name or "").lower() else "Cores"


def stamp(when):
    return when.strftime("%Y-%m-%dT%H:%M:%S")


def run_log(run):
    try:
        began = datetime.datetime.strptime(run, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    return LOGS / f"{began:%Y-%m-%d_%H%M%S}_run.log"


LIVE_WITHIN = 10 * 60


def run_is_live(run):
    log = run_log(run)
    if log is None or not log.exists():
        return False
    try:
        age = datetime.datetime.now().timestamp() - log.stat().st_mtime
        if age >= LIVE_WITHIN:
            return False
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "ran for" not in text


def load(start, end=None):
    if not LEDGER.exists():
        raise SystemExit(f"no ledger at {LEDGER}")
    conn = sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(purchases)")}
    expect = "expect" if "expect" in columns else "NULL"
    where = "run>=?" if end is None else "run>=? AND run<?"
    args = (start,) if end is None else (start, end)
    runs = collections.defaultdict(lambda: {"buys": [], "sells": []})
    for at, run, item, spend, qty, exp in conn.execute(
            f"SELECT at, run, item, spend, qty, {expect} FROM purchases "
            f"WHERE {where} ORDER BY at, id", args):
        if qty:
            runs[run]["buys"].append((at, item, spend or 0, qty, exp))
    for at, run, item, qty, price, proceeds in conn.execute(
            f"SELECT at, run, item, qty, price, proceeds FROM sales "
            f"WHERE {where} ORDER BY at, id", args):
        if qty:
            gross = proceeds if proceeds is not None else (price or 0) * qty
            runs[run]["sells"].append((at, item, units_sold(item, qty),
                                       gross))
    conn.close()
    return runs


def sale_prices(start, end=None):
    conn = sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True)
    where = "run>=?" if end is None else "run>=? AND run<?"
    args = (start,) if end is None else (start, end)
    seen = collections.defaultdict(list)
    for item, qty, proceeds in conn.execute(
            f"SELECT item, qty, proceeds FROM sales WHERE {where}", args):
        if qty and proceeds:
            seen[key(item)].append(proceeds / qty)
    conn.close()
    return {k: sorted(v)[len(v) // 2] for k, v in seen.items() if v}


def close_run(run, book, fallback):
    lots = collections.defaultdict(collections.deque)
    guessed = 0
    buys = collections.deque(sorted(book["buys"]))

    def stock_up(until):
        nonlocal guessed
        while buys and (until is None or buys[0][0] <= until):
            _at, item, spend, qty, expect = buys.popleft()
            k = key(item)
            cost = spend / qty
            if not expect:
                expect = fallback.get(k)
                guessed += qty
            if not expect:
                expect = cost
            lots[k].append([qty, cost, expect, item])

    items = {}

    def row(k, name):
        return items.setdefault(k, {
            "bucket": bucket(name), "sold": 0, "revenue": 0.0,
            "sold_cost": 0.0, "held": 0, "expected": 0.0, "held_cost": 0.0})

    ignored = collections.Counter()
    for at, item, qty, gross in sorted(book["sells"]):
        stock_up(at)
        if not gross:
            continue
        k = key(item)
        each = gross / qty
        left = qty
        while left and lots[k]:
            lot = lots[k][0]
            take = min(left, lot[0])
            r = row(k, item)
            r["sold"] += take
            r["revenue"] += take * each
            r["sold_cost"] += take * lot[1]
            lot[0] -= take
            left -= take
            if lot[0] <= 0:
                lots[k].popleft()
        if left:
            ignored[k] += left
    stock_up(None)

    for k, dq in lots.items():
        for units, cost, expect, name in dq:
            if units <= 0:
                continue
            r = row(k, name)
            r["held"] += units
            r["expected"] += units * expect
            r["held_cost"] += units * cost

    return {"run": run, "items": items, "ignored": ignored,
            "guessed": guessed, "live": run_is_live(run)}


def totals(closed):
    t = {"sold": 0, "revenue": 0.0, "sold_cost": 0.0,
         "held": 0, "expected": 0.0, "held_cost": 0.0}
    for r in closed["items"].values():
        for k in t:
            t[k] += r[k]
    return t


def realised(t):
    return t["revenue"] - t["sold_cost"]


def assumed(t):
    return t["expected"] - t["held_cost"]


def profit(t):
    return realised(t) + assumed(t)


def margin(t):
    gross = t["revenue"] + t["expected"]
    return f"{100 * profit(t) / gross:>7.1f}%" if gross else f"{'--':>8}"


def run_hours(run):
    conn = sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True)
    latest = ""
    for table in ("purchases", "sales"):
        (last,) = conn.execute(
            f"SELECT MAX(at) FROM {table} WHERE run=?", (run,)).fetchone()
        if last and last > latest:
            latest = last
    conn.close()
    try:
        began = datetime.datetime.strptime(run, "%Y-%m-%dT%H:%M:%S")
        ended = datetime.datetime.strptime(latest, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return 0.0
    return max(0.0, (ended - began).total_seconds() / 3600)


def run_spans(since):
    spans = []
    for log in LOGS.glob("*_run.log"):
        try:
            began = datetime.datetime.strptime(
                log.name[:len(datetime.datetime.now().strftime(LOG_STAMP))],
                LOG_STAMP)
        except ValueError:
            continue
        if began < since:
            continue
        try:
            found = ENDED.search(log.read_text(encoding="utf-8",
                                               errors="replace"))
            ended = datetime.datetime.fromtimestamp(log.stat().st_mtime)
        except OSError:
            continue
        if found:
            clock = datetime.time(*(int(part) for part in found.groups()))
            ended = datetime.datetime.combine(began.date(), clock)
            if ended < began:
                ended += datetime.timedelta(days=1)
        spans.append((began, ended))
    return spans


def up_hours(spans, begin, end):
    end = min(end, datetime.datetime.now())
    seconds = sum(max(0.0, (min(ended, end) - max(began, begin)).total_seconds())
                  for began, ended in spans)
    return seconds / 3600


def line(char="-", width=96):
    print(char * width)


def close_window(start, end=None):
    runs = load(start, end)
    fallback = sale_prices(start, end)
    return [close_run(run, runs[run], fallback) for run in sorted(runs)]


def by_day(count=DAYS_BACK):
    today = datetime.date.today()
    first = today - datetime.timedelta(days=count - 1)
    print(f"LAST {count} DAYS -- {first:%Y-%m-%d} to {today:%Y-%m-%d}, each "
          f"day midnight to midnight")
    print("a run counts on the day it was LAUNCHED; each run is closed on "
          "its own stock, unsold lots at the price they were bought against")
    print("")
    print(f"{'day':<26}{'hours':>8}{'profit':>15}{'realised':>15}"
          f"{'assumed':>15}{'units':>8}{'margin':>8}{'an hour':>14}")
    line(width=118)
    grand = collections.Counter()
    spans = run_spans(datetime.datetime.combine(first, datetime.time.min)
                      - datetime.timedelta(days=1))
    all_up = 0.0
    for back in range(count - 1, -1, -1):
        day = today - datetime.timedelta(days=back)
        begin = datetime.datetime.combine(day, datetime.time.min)
        end = begin + datetime.timedelta(days=1)
        closed = close_window(stamp(begin), stamp(end))
        t = collections.Counter()
        for c in closed:
            t.update(totals(c))
        grand.update(t)
        up = up_hours(spans, begin, end)
        all_up += up
        label = f"{day:%a %Y-%m-%d}" + (" (so far)" if not back else "")
        print(f"{label:<26}{up:>7.2f}h{profit(t):>15,.0f}"
              f"{realised(t):>15,.0f}{assumed(t):>15,.0f}"
              f"{t['sold'] + t['held']:>8,}{margin(t)}"
              f"{profit(t) / up if up else 0:>14,.0f}")
    line("=", width=118)
    print(f"{f'{count} DAYS':<26}{all_up:>7.2f}h{profit(grand):>15,.0f}"
          f"{realised(grand):>15,.0f}{assumed(grand):>15,.0f}"
          f"{grand['sold'] + grand['held']:>8,}{margin(grand)}"
          f"{profit(grand) / all_up if all_up else 0:>14,.0f}")


def report_day():
    start = datetime.datetime.now().replace(hour=0, minute=0, second=0,
                                            microsecond=0)
    now = datetime.datetime.now().strftime("%H:%M")
    print(f"PROFIT SUMMARY -- runs launched since {start:%Y-%m-%d} 00:00 "
          f"(as of {now})")
    print("realised = bought and sold by the same run; assumed = bought and "
          "still held, at the price it was bought against")
    print("")
    closed = close_window(stamp(start))
    if not closed:
        print("  no run has traded today.")
        return

    items = {}
    for c in closed:
        for k, r in c["items"].items():
            acc = items.setdefault(k, {"bucket": r["bucket"]})
            for f in ("sold", "revenue", "sold_cost", "held", "expected",
                      "held_cost"):
                acc[f] = acc.get(f, 0) + r[f]

    print(f"{'item':<26}{'profit':>15}{'realised':>15}{'assumed':>15}"
          f"{'units':>8}{'margin':>8}{'cost':>16}")
    line(width=103)
    groups = {"Cores": collections.Counter(), "Chaos": collections.Counter()}
    for k, r in sorted(items.items(),
                       key=lambda kv: -(kv[1]["revenue"] + kv[1]["expected"])):
        cost = r["sold_cost"] + r["held_cost"]
        print(f"{k[:25]:<26}{profit(r):>15,.0f}{realised(r):>15,.0f}"
              f"{assumed(r):>15,.0f}{r['sold'] + r['held']:>8,}{margin(r)}"
              f"{cost:>16,.0f}")
        groups[r["bucket"]].update({f: r[f] for f in (
            "sold", "revenue", "sold_cost", "held", "expected", "held_cost")})
    line(width=103)
    total = collections.Counter()
    for label in ("Cores", "Chaos"):
        g = groups[label]
        total.update(g)
        if not (g["sold"] + g["held"]):
            continue
        print(f"{label:<26}{profit(g):>15,.0f}{realised(g):>15,.0f}"
              f"{assumed(g):>15,.0f}{g['sold'] + g['held']:>8,}{margin(g)}"
              f"{g['sold_cost'] + g['held_cost']:>16,.0f}")
    line("=", width=103)
    print(f"{'TOTAL':<26}{profit(total):>15,.0f}{realised(total):>15,.0f}"
          f"{assumed(total):>15,.0f}{total['sold'] + total['held']:>8,}"
          f"{margin(total)}{total['sold_cost'] + total['held_cost']:>16,.0f}")

    print("")
    print("by run:")
    print(f"  {'launched':<21}{'units':>7}{'realised':>15}{'assumed':>15}"
          f"{'profit':>15}{'hours':>7}{'an hour':>15}")
    all_hours = 0.0
    for c in closed:
        t = totals(c)
        ran = run_hours(c["run"])
        all_hours += ran
        rate = f"{profit(t) / ran:>15,.0f}" if ran else f"{'--':>15}"
        tag = "  live, open stock at its expected price" if c["live"] else ""
        print(f"  {c['run']:<21}{t['sold'] + t['held']:>7,}{realised(t):>15,.0f}"
              f"{assumed(t):>15,.0f}{profit(t):>15,.0f}{ran:>7.2f}{rate}{tag}")
    line(width=103)
    print(f"  {len(closed)} run(s) trading for {all_hours:.2f} hour(s)"
          f"{'':>40}{profit(total) / all_hours if all_hours else 0:>15,.0f} an hour")
    print("  hours are launch to last trade, so a run still going is short by "
          "whatever it has not traded in yet")

    ignored = collections.Counter()
    for c in closed:
        ignored.update(c["ignored"])
    if ignored:
        print("")
        print("sold by a run that did not buy it, so already counted by the "
              "run that did (or never bought by the script):")
        for k, units in ignored.most_common():
            print(f"  {k:<26}{units:>8,} units")

    live = [c for c in closed if c["live"]]
    for c in live:
        held = {k: r for k, r in c["items"].items() if r["held"]}
        if not held:
            continue
        print("")
        print(f"open on the live run {c['run']}, counted above at the price "
              f"each lot was bought against:")
        for k, r in sorted(held.items(), key=lambda kv: -kv[1]["held_cost"]):
            print(f"  {k:<26}{r['held']:>8,} units  cost {r['held_cost']:>15,.0f}"
                  f"  expected {r['expected']:>15,.0f}  "
                  f"({assumed(r):>+13,.0f})")

    guessed = sum(c["guessed"] for c in closed)
    if guessed:
        print("")
        print(f"{guessed:,} unit(s) were bought before the ledger recorded the "
              f"price they were bought against; those are closed at the "
              f"day's median sale price for the item, or at cost if it "
              f"never sold")

    print("")
    print("revenue is what the collections actually paid; the shop's sales "
          "fee is 0.0%, so that is the full sale price")


BOARD_HEAD = re.compile(r"^  board after pass (\d+):$", re.M)
LAUNCH_HEAD = re.compile(r"^ +bought/u +listed/u", re.M)
BOARD_LINE = re.compile(r"^(\s{4,}\d+\s{2,}(.+?)\s+x([\d,]+)\s+([\d,]+|-)\s+([\d,]+)"
                        r"\s+(?:[-+]?[\d.]+%|-)\s+)([\d,]+)(\s*)$")
BOARD_COLUMNS = re.compile(r"^(\s+bought/u\s+listed/u\s+margin\s+row price)\s*$")
ITEM_COLUMNS = re.compile(r"^(\s{4}item\s+rows\s+units\s+listed)\s*$")
ITEM_LINE = re.compile(r"^(\s{4}(\S.*?)\s{2,}\d+\s+[\d,]+\s+[\d,]+)\s*$")
PROFIT_WIDTH = 16
BOARD_INDENT = "    "
BOARD_LABEL = 49
BOARD_NUMBER = 16


def row_total(line, gains):
    found = BOARD_COLUMNS.match(line)
    if found:
        return f"{found.group(1)}{'profit if sold':>{PROFIT_WIDTH}}"
    found = ITEM_COLUMNS.match(line)
    if found:
        return f"{found.group(1)}{'profit if sold':>{PROFIT_WIDTH}}"
    found = ITEM_LINE.match(line)
    if found:
        item = found.group(2)
        gain = gains.get("board" if item == "board" else key_item(item))
        return f"{found.group(1)}{(f'{gain:,}' if gain is not None else '-'):>{PROFIT_WIDTH}}"
    found = BOARD_LINE.match(line)
    if not found:
        return line
    name, cost = found.group(2), found.group(4)
    qty, each, last = (int(found.group(i).replace(",", "")) for i in (3, 5, 6))
    head = found.group(1).rstrip()
    price = f"{head} {last * qty:>14,}" if last == each else f"{head} {last:>14,}"
    if cost == "-":
        return f"{price}{'-':>{PROFIT_WIDTH}}"
    gain = (each - int(cost.replace(",", ""))) * qty * pack(name)
    gains[key_item(name)] = gains.get(key_item(name), 0) + gain
    gains["board"] = gains.get("board", 0) + gain
    return f"{price}{gain:>{PROFIT_WIDTH},}"


def key_item(name):
    return PACK.sub("", name).strip()


def report_board():
    logs = sorted(LOGS.glob("*_run.log"), key=lambda f: f.stat().st_mtime)
    if not logs:
        return
    log = logs[-1]
    text = log.read_text(encoding="utf-8", errors="replace")
    heads = list(BOARD_HEAD.finditer(text))
    if heads:
        head = heads[-1]
        title = f"board after pass {head.group(1)}"
        start = head.end() + 1
    else:
        head = LAUNCH_HEAD.search(text)
        if head is None:
            print(f"ROWS -- {log.name}: no board printed yet")
            return
        title = "board at launch, no pass finished yet"
        start = head.start()
    print(f"ROWS -- {log.name}, {title}"
          f"{'' if 'ran for' in text else ' (live)'}")
    gains = {}
    for row in text[start:].splitlines():
        if row.startswith("-- pass") or row.startswith("  server clock"):
            break
        if row.strip():
            print(row_total(row, gains))
    import networth
    networth.summary(log, BOARD_INDENT, BOARD_LABEL, BOARD_NUMBER, PROFIT_WIDTH)


PASS_HEAD = re.compile(r"^-- pass (\d+) --$", re.M)
PASS_COLUMNS = re.compile(r"^  core\s+rows\s+(buy/u\s+sell/u\s+)?margin\s+wants\s+short\?\s*$")
PASS_LINE = re.compile(r"^  (\S.*?)\s{2,}(\d+)\s+(.*?)\s*$")
PRICE_LINE = re.compile(r"^  (\S.*?) ([\d,]+) - (\S.*?) ([\d,]+) = (-?[\d,]+)$")
MARKET_LINE = re.compile(r"^  (\S.*?)\s{2,}([\d,]+)\s*$")
SET_WORD = re.compile(r"\s*\bSet\b\s*")


def core_key(name):
    return re.sub(r"[^a-z0-9]", "", SET_WORD.sub("", PACK.sub("", name)).lower())


def last_prices(lines, upto):
    priced = {}
    for line in lines[:upto]:
        found = PRICE_LINE.match(line)
        if found:
            priced[core_key(found.group(1))] = (number(found.group(4)),
                                                number(found.group(2)))
    return priced


def launch_prices(lines):
    out, inside = {}, False
    for line in lines:
        if line.startswith("market prices:"):
            inside, out = True, {}
            continue
        if inside:
            found = MARKET_LINE.match(line)
            if not found:
                inside = False
                continue
            out[found.group(1)] = number(found.group(2))
    pairs = {}
    for name, price in out.items():
        pairs.setdefault(core_key(name), {})["set" if "Set" in name else "core"] = price
    return pairs


def number(text):
    return int(text.replace(",", ""))


def report_market():
    logs = sorted(LOGS.glob("*_run.log"), key=lambda f: f.stat().st_mtime)
    if not logs:
        return
    log = logs[-1]
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    heads = [i for i, line in enumerate(lines) if PASS_HEAD.match(line)]
    if not heads:
        print(f"MARKET -- {log.name}: no pass finished yet")
        return
    start = heads[-1]
    number_of_pass = PASS_HEAD.match(lines[start]).group(1)
    written = datetime.datetime.fromtimestamp(log.stat().st_mtime)
    table, columns = [], None
    for i in range(start, len(lines)):
        if columns is None:
            found = PASS_COLUMNS.match(lines[i])
            if found:
                columns = bool(found.group(1))
            continue
        found = PASS_LINE.match(lines[i])
        if not found:
            break
        table.append(found.groups())
    if columns is None:
        print(f"MARKET -- {log.name}: pass {number_of_pass} printed no core table")
        return
    print(f"MARKET -- {log.name}, pass {number_of_pass}, log last written {written:%H:%M}"
          f"{'' if any('ran for' in l for l in lines[-40:]) else ' (live)'}")
    print("buy/u is what a unit costs on the Purchase tab, sell/u what the other side of the "
          "pair lists for; margin is sell minus buy, wants the rows that margin is worth")
    print("")
    print(f"{'core':<30}{'rows':>6}{'buy/u':>12}{'sell/u':>12}{'margin':>10}{'margin %':>10}"
          f"{'wants':>7}   short?{'' if columns else '  priced'}")
    line(width=100)
    priced = None if columns else last_prices(lines, start)
    launch = None if columns else launch_prices(lines)
    for core, rows, rest in table:
        cells = rest.split()
        if columns:
            buy, sell, margin, wants = cells[:4]
            mark = " ".join(cells[4:])
            when = ""
        else:
            margin, wants = cells[:2]
            mark = " ".join(cells[2:])
            got = priced.get(core_key(core))
            if got:
                buy, sell = (f"{got[0]:,}", f"{got[1]:,}")
                when = "last resupply"
            else:
                pair = launch.get(core_key(core), {})
                b, s = pair.get("core"), pair.get("set")
                if b is not None and s is not None and margin != "-" and (
                        (s - b < 0) != (number(margin) < 0)):
                    b, s = s, b
                buy = "-" if b is None else f"{b:,}"
                sell = "-" if s is None else f"{s:,}"
                when = "launch"
        pct = "-"
        if buy != "-" and sell != "-" and margin != "-":
            pct = f"{number(margin) / number(sell) * 100:.1f}%"
        print(f"{core:<30}{rows:>6}{buy:>12}{sell:>12}{margin:>10}{pct:>10}{wants:>7}   "
              f"{mark:<6}{'' if columns else '  ' + when}")
    if not columns:
        print("")
        print("this run prints only the margin each pass; buy/u and sell/u are from the "
              "last time it priced that core (its last resupply, else launch)")


def main():
    by_day()
    print("")
    print("")
    report_day()
    print("")
    print("")
    report_board()
    print("")
    print("")
    report_market()


if __name__ == "__main__":
    main()
