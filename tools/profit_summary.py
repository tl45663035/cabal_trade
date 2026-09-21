import collections
import datetime
import io
import json
import pathlib
import re
import sys
import sqlite3
import zoneinfo

ROOT = pathlib.Path(__file__).resolve().parent.parent
LEDGER = ROOT / "src_1080p" / "sales.db"
LOGS = ROOT / "src_1080p" / "logs"
CLOCK = zoneinfo.ZoneInfo("America/Chicago")


def central(when):
    return when.astimezone(CLOCK).replace(tzinfo=None)


def from_central(when):
    return when.replace(tzinfo=CLOCK).astimezone().replace(tzinfo=None)


def central_now():
    return central(datetime.datetime.now())
PACK = re.compile(r"\bX\s*[\d,]+", re.I)
SETTINGS = json.loads((ROOT / "src_1080p" / "config.json")
                      .read_text(encoding="utf-8"))
KNOBS = SETTINGS["tools"]
DAYS_BACK = int(KNOBS["profit_days_back"])
ENDED = re.compile(r"ended (\d\d):(\d\d):(\d\d), ran for")
LOG_STAMP = "%Y-%m-%d_%H%M%S"


def key(name):
    stripped = PACK.sub(" ", name or "")
    return re.sub(r"[^a-z]", "", stripped.lower()).replace("set", "")


CASH = {key(name) for name
        in SETTINGS["resupply"]["cash_shop"]["rows"]}


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
    if key(name) in CASH:
        return "Cash"
    return "Chaos" if "chaos" in (name or "").lower() else "Cores"


def stamp(when):
    return when.strftime("%Y-%m-%dT%H:%M:%S")


def run_log(run):
    try:
        began = datetime.datetime.strptime(run, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    return LOGS / f"{began:%Y-%m-%d_%H%M%S}_run.log"


LIVE_WITHIN = float(KNOBS["live_within"])


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


def listed_now():
    import networth
    log = networth.newest_log()
    if log is None:
        return {}, None, None
    _market, board, _unread, _balance, bought = networth.read(log)
    units = collections.Counter()
    worth = collections.Counter()
    for _index, name, qty, each, _listed, _cost in board:
        k = key(name)
        n = qty * pack(name)
        units[k] += n
        worth[k] += n * each
    listed = {k: worth[k] / units[k] for k in units if units[k]}
    for name, cores, _spent in bought:
        units[key(name)] += cores
    return listed, units, log

BUDGET = re.compile(r"^relisting rows \d+-\d+ for ([\d.]+) minute\(s\)")

PASS_LEFT = re.compile(r"^  pass \d+: .*?([\d.]+) minute\(s\) left")

def log_launch(log):
    try:
        return datetime.datetime.strptime(
            log.name[:len(datetime.datetime.now().strftime(LOG_STAMP))],
            LOG_STAMP)
    except ValueError:
        return None

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


MARKET_HEAD = re.compile(r"^market prices:$")
MARKET_ROW = re.compile(r"^  (\S.*?)\s{2,}([\d,]+)$")
VOUCHER_COST = re.compile(r"^  a \w+ voucher costs ([\d,]+)$")
SETTINGS = json.loads((ROOT / "src_1080p" / "config.json")
                      .read_text(encoding="utf-8"))
VOUCHER_FLOORS = SETTINGS["run"]["voucher_floor"]
VOUCHER_PARTS = int(json.loads((ROOT / "src_1080p" / "calibration.json")
                               .read_text(encoding="utf-8"))
                    ["game_facts"]["voucher_cash"])


def is_set(name):
    return "set" in (name or "").lower()


def voucher_ratio(name):
    folded = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    best, found = 0, 0
    for item, rule in VOUCHER_FLOORS.items():
        whole = re.sub(r"[^a-z0-9]", "", item.lower())
        tries = [[whole]] + [[want] if isinstance(want, str) else list(want)
                             for want in rule.get("any", [])]
        for parts in tries:
            folds = [re.sub(r"[^a-z0-9]", "", str(p).lower()) for p in parts]
            if not folds or not all(f and f in folded for f in folds):
                continue
            weight = sum(len(f) for f in folds)
            if weight > best:
                best, found = weight, int(rule["ratio"])
    return found


def launch_floors(text):
    prices, voucher, reading = {}, 0, False
    for raw in text.splitlines():
        if MARKET_HEAD.match(raw):
            reading = True
            continue
        found = VOUCHER_COST.match(raw)
        if found:
            voucher = number(found.group(1))
        if not reading:
            continue
        found = MARKET_ROW.match(raw)
        if not found:
            if raw and not raw.startswith("  "):
                reading = False
            continue
        prices.setdefault(key(found.group(1)), {})[
            is_set(found.group(1))] = number(found.group(2))
    return prices, voucher


def floor_for(name, prices, voucher):
    ratio = voucher_ratio(name)
    if ratio:
        return voucher * ratio // VOUCHER_PARTS
    pair = prices.get(key(name), {})
    return pair.get(not is_set(name), 0)


def opening_stock(text):
    import networth
    table, done = [], False
    for raw in text.splitlines():
        if BUDGET.match(raw):
            done = True
            break
        found = networth.BOARD_ROW.match(raw)
        if found:
            name = found.group(2).strip()
            table.append((name, number(found.group(3)) * pack(name),
                          number(found.group(5))))
    return table if done else []


def run_logs(since):
    found = []
    for log in LOGS.glob("*_run.log"):
        launched = log_launch(log)
        if launched is None or launched < since:
            continue
        found.append((launched, log))
    return sorted(found)


def ledger_runs(start):
    if not LEDGER.exists():
        raise SystemExit(f"no ledger at {LEDGER}")
    conn = sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True)
    buys = collections.defaultdict(list)
    sells = collections.defaultdict(list)
    for at, run, item, spend, qty in conn.execute(
            "SELECT at, run, item, spend, qty FROM purchases WHERE at>=? "
            "ORDER BY at, id", (start,)):
        if qty:
            buys[run].append({"at": at, "item": item, "k": key(item),
                              "units": qty, "cost": (spend or 0) / qty})
    for at, run, item, qty, price, proceeds in conn.execute(
            "SELECT at, run, item, qty, price, proceeds FROM sales WHERE at>=? "
            "ORDER BY at, id", (start,)):
        if qty:
            gross = proceeds if proceeds is not None else (price or 0) * qty
            sells[run].append({"at": at, "item": item, "k": key(item),
                               "units": units_sold(item, qty),
                               "gross": gross or 0})
    conn.close()
    return buys, sells


def match_log(run, logs):
    try:
        began = datetime.datetime.strptime(run, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    near = [(abs((launched - began).total_seconds()), log)
            for launched, log in logs]
    near.sort()
    return near[0][1] if near and near[0][0] <= LAUNCH_SLACK else None


LAUNCH_SLACK = int(KNOBS["launch_slack"])


def close_runs(since):
    logs = run_logs(since - datetime.timedelta(days=1))
    buys, sells = ledger_runs(stamp(since))
    runs = []
    for run in sorted(set(buys) | set(sells)):
        log = match_log(run, logs)
        text = ""
        if log is not None:
            try:
                text = log.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
        prices, voucher = launch_floors(text)
        stock = collections.defaultdict(collections.deque)
        opened = collections.Counter()
        for name, count, each in opening_stock(text):
            basis = floor_for(name, prices, voucher) or each
            stock[key(name)].append({"units": count, "cost": basis,
                                     "item": name, "opening": True})
            opened[key(name)] += count
        events = ([(b["at"], 0, b) for b in buys.get(run, [])]
                  + [(s["at"], 1, s) for s in sells.get(run, [])])
        sold, unmatched = [], collections.Counter()
        for _at, kind, event in sorted(events, key=lambda e: (e[0], e[1])):
            if kind == 0:
                stock[event["k"]].append({"units": event["units"],
                                          "cost": event["cost"],
                                          "item": event["item"],
                                          "opening": False})
                continue
            left, cost, taken = event["units"], 0.0, 0
            queue = stock[event["k"]]
            while left and queue:
                lot = queue[0]
                take = min(left, lot["units"])
                cost += take * lot["cost"]
                lot["units"] -= take
                left -= take
                taken += take
                if lot["units"] <= 0:
                    queue.popleft()
            if left:
                unmatched[event["k"]] += left
            if taken:
                each = event["gross"] / event["units"]
                sold.append({"at": event["at"], "item": event["item"],
                             "k": event["k"], "bucket": bucket(event["item"]),
                             "units": taken, "revenue": each * taken,
                             "cost": cost})
        carried = {k: sum(lot["units"] for lot in queue)
                   for k, queue in stock.items()
                   if sum(lot["units"] for lot in queue)}
        carried_cost = {k: sum(lot["units"] * lot["cost"] for lot in queue)
                        for k, queue in stock.items() if carried.get(k)}
        runs.append({"run": run, "log": log, "sold": sold,
                     "unmatched": unmatched, "carried": carried,
                     "carried_cost": carried_cost, "opened": opened,
                     "bought": sum(b["units"] for b in buys.get(run, [])),
                     "spent": sum(b["units"] * b["cost"]
                                  for b in buys.get(run, []))})
    return runs


def sold_totals(sales):
    t = collections.Counter()
    for s in sales:
        t["units"] += s["units"]
        t["revenue"] += s["revenue"]
        t["cost"] += s["cost"]
    return t


def gain(t):
    return t["revenue"] - t["cost"]


BUCKETS = ("Cores", "Chaos", "Cash")
DAY_WIDTH = 117


def per_unit(t, field):
    return f"{t[field] / t['units']:,.0f}" if t["units"] else "-"


def rate(t):
    return f"{100 * gain(t) / t['revenue']:>7.1f}%" if t["revenue"] else f"{'--':>8}"


def all_sales(runs, begin=None, end=None):
    first = None if begin is None else stamp(begin)
    last = None if end is None else stamp(end)
    out = []
    for r in runs:
        for s in r["sold"]:
            if first is not None and s["at"] < first:
                continue
            if last is not None and s["at"] >= last:
                continue
            out.append(s)
    return out


def open_book(count=DAYS_BACK):
    today = central_now().date()
    first = today - datetime.timedelta(days=count - 1)
    since = from_central(datetime.datetime.combine(first, datetime.time.min))
    runs = close_runs(since)
    return {"first": first, "today": today, "runs": runs}


def by_day(book):
    count = DAYS_BACK
    first, today = book["first"], book["today"]
    print(f"LAST {count} DAYS -- {first:%Y-%m-%d} to {today:%Y-%m-%d}, each "
          f"day midnight to midnight Central")
    print("")
    print(f"{'day':<26}{'hours':>8}{'profit':>15}{'revenue':>15}"
          f"{'cost':>15}{'units':>8}{'margin':>8}{'an hour':>14}")
    line(width=118)
    grand = collections.Counter()
    spans = run_spans(from_central(datetime.datetime.combine(
        first, datetime.time.min)) - datetime.timedelta(days=1))
    all_up = 0.0
    for back in range(count - 1, -1, -1):
        day = today - datetime.timedelta(days=back)
        begin = from_central(datetime.datetime.combine(day,
                                                       datetime.time.min))
        end = from_central(datetime.datetime.combine(
            day + datetime.timedelta(days=1), datetime.time.min))
        t = sold_totals(all_sales(book["runs"], begin, end))
        grand.update(t)
        up = up_hours(spans, begin, end)
        all_up += up
        label = f"{day:%a %Y-%m-%d}" + (" (so far)" if not back else "")
        print(f"{label:<26}{up:>7.2f}h{gain(t):>15,.0f}"
              f"{t['revenue']:>15,.0f}{t['cost']:>15,.0f}"
              f"{t['units']:>8,}{rate(t)}"
              f"{gain(t) / up if up else 0:>14,.0f}")
    line("=", width=118)
    print(f"{f'{count} DAYS':<26}{all_up:>7.2f}h{gain(grand):>15,.0f}"
          f"{grand['revenue']:>15,.0f}{grand['cost']:>15,.0f}"
          f"{grand['units']:>8,}{rate(grand)}"
          f"{gain(grand) / all_up if all_up else 0:>14,.0f}")


def report_day(book):
    start = from_central(central_now().replace(hour=0, minute=0, second=0,
                                               microsecond=0))
    now = central_now().strftime("%H:%M")
    print(f"PROFIT SUMMARY -- sold since {central(start):%Y-%m-%d} 00:00 "
          f"Central (as of {now} Central)")
    print("")
    sales = all_sales(book["runs"], start)
    if not sales:
        print("  nothing has sold today.")
        return

    rows = {}
    for s in sales:
        acc = rows.setdefault(s["k"], collections.Counter())
        acc["units"] += s["units"]
        acc["revenue"] += s["revenue"]
        acc["cost"] += s["cost"]
        rows[s["k"]] = acc
        rows[s["k"]]["where"] = s["bucket"]

    print(f"{'item':<30}{'profit':>15}{'revenue':>15}{'cost':>15}"
          f"{'units':>8}{'bought/u':>13}{'sold/u':>13}{'margin':>8}")
    line(width=DAY_WIDTH)
    groups = {name: collections.Counter() for name in BUCKETS}
    for k, r in sorted(rows.items(), key=lambda kv: -gain(kv[1])):
        print(f"{k[:29]:<30}{gain(r):>15,.0f}{r['revenue']:>15,.0f}"
              f"{r['cost']:>15,.0f}{r['units']:>8,}{per_unit(r, 'cost'):>13}"
              f"{per_unit(r, 'revenue'):>13}{rate(r)}")
        for field in ("units", "revenue", "cost"):
            groups[r["where"]][field] += r[field]
    line(width=DAY_WIDTH)
    total = collections.Counter()
    for label in BUCKETS:
        g = groups[label]
        for field in ("units", "revenue", "cost"):
            total[field] += g[field]
        if not g["units"]:
            continue
        print(f"{label:<30}{gain(g):>15,.0f}{g['revenue']:>15,.0f}"
              f"{g['cost']:>15,.0f}{g['units']:>8,}{per_unit(g, 'cost'):>13}"
              f"{per_unit(g, 'revenue'):>13}{rate(g)}")
    line("=", width=DAY_WIDTH)
    print(f"{'TOTAL':<30}{gain(total):>15,.0f}{total['revenue']:>15,.0f}"
          f"{total['cost']:>15,.0f}{total['units']:>8,}"
          f"{per_unit(total, 'cost'):>13}{per_unit(total, 'revenue'):>13}"
          f"{rate(total)}")

    print("")
    print("by run, on what each run sold:")
    print(f"  {'launched':<21}{'units':>7}{'revenue':>15}{'cost':>15}"
          f"{'profit':>15}{'hours':>7}{'an hour':>15}")
    all_hours = 0.0
    shown = 0
    for r in book["runs"]:
        today_sales = [s for s in r["sold"] if s["at"] >= stamp(start)]
        if not today_sales and r["run"][:10] != stamp(start)[:10]:
            continue
        t = sold_totals(today_sales)
        ran = run_hours(r["run"])
        all_hours += ran
        shown += 1
        each = f"{gain(t) / ran:>15,.0f}" if ran else f"{'--':>15}"
        tag = "  live" if run_is_live(r["run"]) else ""
        print(f"  {r['run']:<21}{t['units']:>7,}{t['revenue']:>15,.0f}"
              f"{t['cost']:>15,.0f}{gain(t):>15,.0f}{ran:>7.2f}{each}{tag}")
    line(width=103)
    print(f"  {shown} run(s) trading for {all_hours:.2f} hour(s)"
          f"{'':>40}{gain(total) / all_hours if all_hours else 0:>15,.0f} an hour")

    unmatched = collections.Counter()
    for r in book["runs"]:
        if r["run"] >= stamp(start) or any(s["at"] >= stamp(start)
                                           for s in r["sold"]):
            unmatched.update(r["unmatched"])
    if unmatched:
        print("")
        print("sold with no lot to match, left out of the totals:")
        for k, n in unmatched.most_common():
            print(f"  {k:<26}{n:>8,} units")

    live = [r for r in book["runs"] if run_is_live(r["run"])]
    if live:
        r = live[-1]
        held = {k: n for k, n in r["carried"].items() if n}
        if held:
            print("")
            print("held by the live run, to be counted when it sells or "
                  "carried to the next run at that launch's floor:")
            for k, n in sorted(held.items(), key=lambda kv: -kv[1]):
                basis = r["carried_cost"].get(k, 0)
                print(f"  {k:<26}{n:>8,} units  basis {basis:>15,.0f}")

    print("")


BOARD_HEAD = re.compile(r"^  board after pass (\d+):$", re.M)
LAUNCH_HEAD = re.compile(r"^ +bought/u +listed/u", re.M)
BOARD_LINE = re.compile(r"^(\s{4,}\d+\s{2,}(.+?)\s+x([\d,]+)\s+([\d,]+|-)\s+([\d,]+)"
                        r"\s+(?:[-+]?[\d,.]+%?|-)\s+)([\d,]+)(\s*)$")
BOARD_COLUMNS = re.compile(r"^(\s+bought/u\s+listed/u\s+margin\s+row price)\s*$")
ITEM_COLUMNS = re.compile(r"^(\s{4}item\s+rows\s+units\s+listed)\s*$")
ITEM_LINE = re.compile(r"^(\s{4}(\S.*?)\s{2,}\d+\s+[\d,]+\s+[\d,]+)\s*$")
PROFIT_WIDTH = 16
MARGIN_WIDTH = 10
MARGIN_COLUMNS = re.compile(r"^(\s+bought/u\s+listed/u)\s+margin"
                            r"(\s+row price.*)$")
MARGIN_ROW = re.compile(r"^(\s{4,}\d+\s{2,}.+?\s+x[\d,]+\s+([\d,]+|-)"
                        r"\s+([\d,]+)\s+)(?:[-+]?[\d,.]+%?|-)(\s+.*)$")
BOARD_INDENT = "    "
BOARD_LABEL = 49
BOARD_NUMBER = 16


def raw_margin(line):
    found = MARGIN_COLUMNS.match(line)
    if found:
        return (f"{found.group(1)} {'margin':>{MARGIN_WIDTH}}"
                f"{found.group(2)}")
    found = MARGIN_ROW.match(line)
    if found is None:
        return line
    cost, each = found.group(2), int(found.group(3).replace(",", ""))
    shown = "-" if cost == "-" else f"{each - int(cost.replace(',', '')):+,}"
    return (f"{found.group(1).rstrip()} {shown:>{MARGIN_WIDTH}}"
            f"{found.group(4)}")


def row_total(line, gains):
    line = raw_margin(line)
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


TRACE_HEAD = re.compile(r"^  board during pass (\d+), at row (\d+) of "
                        r"(\d+)-(\d+), read (\S+)$")


def trace_for(log):
    path = log.with_name(f"{log.stem}_board.log")
    return path if path.exists() else None


def report_trace(log, text):
    trace = trace_for(log)
    if trace is None:
        return False
    body = trace.read_text(encoding="utf-8", errors="replace")
    found = TRACE_HEAD.match(body.splitlines()[0] if body else "")
    if found is None:
        return False
    printed = [int(head.group(1)) for head in BOARD_HEAD.finditer(text)]
    if printed and max(printed) >= int(found.group(1)):
        return False
    print(f"ROWS -- {trace.name}, board during pass {found.group(1)} at row "
          f"{found.group(2)}, read {found.group(5)}"
          f"{'' if 'ran for' in text else ' (live)'}")
    for line in body.splitlines()[1:]:
        print(raw_margin(line))
    import networth
    networth.summary(log, BOARD_INDENT, BOARD_LABEL, BOARD_NUMBER,
                     PROFIT_WIDTH, board_text=body)
    return True


def report_board():
    logs = sorted(LOGS.glob("*_run.log"), key=lambda f: f.stat().st_mtime)
    if not logs:
        return
    log = logs[-1]
    text = log.read_text(encoding="utf-8", errors="replace")
    if report_trace(log, text):
        return
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
    written = datetime.datetime.fromtimestamp(log.stat().st_mtime)
    behind = 0
    for back, start in enumerate(reversed(heads)):
        stop = heads[heads.index(start) + 1] if start != heads[-1] else len(lines)
        table, columns = [], None
        for i in range(start, stop):
            if columns is None:
                found = PASS_COLUMNS.match(lines[i])
                if found:
                    columns = bool(found.group(1))
                continue
            found = PASS_LINE.match(lines[i])
            if not found:
                break
            table.append(found.groups())
        if columns is not None and table:
            number_of_pass = PASS_HEAD.match(lines[start]).group(1)
            behind = back
            break
    else:
        newest = PASS_HEAD.match(lines[heads[-1]]).group(1)
        print(f"MARKET -- {log.name}: no pass has printed a core table yet "
              f"(newest is pass {newest})")
        return
    stale = ""
    if behind:
        newest = PASS_HEAD.match(lines[heads[-1]]).group(1)
        stale = (f"; pass {newest} is under way and has not priced the cores "
                 f"yet, so this is the last table it printed")
    print(f"MARKET -- {log.name}, pass {number_of_pass}, log last written "
          f"{central(written):%H:%M} Central"
          f"{'' if any('ran for' in l for l in lines[-KNOBS["log_tail_lines"]:]) else ' (live)'}{stale}")
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


SIZES = tuple((int(KNOBS[f"short_{mark.lower()}"]), mark)
              for mark in ("B", "M", "K"))
SHORT_PLACES = int(KNOBS["short_places"])
SHORT_GAP = int(KNOBS["short_gap"])
GROUPED = re.compile(r"(?<![\w.,])(-?\d{1,3}(?:,\d{3})+)(?!\d)(?!,\d)")
RULE = re.compile(r"^\s*([-=])\1{4,}\s*$")


def short(value):
    size = abs(value)
    for cut, mark in SIZES:
        if size < cut:
            continue
        body = f"{size / cut:.{SHORT_PLACES}f}"
        if float(body) >= SIZES[-1][0]:
            over = [step for step in SIZES if step[0] > cut]
            if over:
                cut, mark = min(over)
                body = f"{size / cut:.{SHORT_PLACES}f}"
        return f"{'-' if value < 0 else ''}{body}{mark}"
    return f"{value:,}"


def shorten(line, pad=True):
    def swap(found):
        text = found.group(1)
        small = short(int(text.replace(",", "")))
        return f"{small:>{len(text)}}" if pad else small
    return GROUPED.sub(swap, line)


def tabular(line):
    return "  " in line.strip() or RULE.match(line) is not None


def squeeze(block):
    body = [line for line in block if RULE.match(line) is None]
    if not body:
        return block
    width = max(len(line) for line in body)
    padded = [line.ljust(width) for line in body]
    keep, run = [], 0
    for column in range(width):
        if all(line[column] == " " for line in padded):
            run += 1
            if run > SHORT_GAP:
                continue
        else:
            run = 0
        keep.append(column)
    done = ["".join(line[c] for c in keep).rstrip() for line in padded]
    edge = max(len(line) for line in done)
    out, taken = [], iter(done)
    for line in block:
        found = RULE.match(line)
        out.append(found.group(1) * edge if found else next(taken))
    return out


def tighten(text, close=True):
    if not close:
        return "\n".join(shorten(line, pad=False)
                         for line in text.splitlines())
    lines = [shorten(line) for line in text.splitlines()]
    out, block = [], []
    for line in lines + [None]:
        if line is not None and line.strip() and tabular(line):
            block.append(line)
            continue
        if len(block) > 1:
            out.extend(squeeze(block))
        else:
            out.extend(block)
        block = []
        if line is not None:
            out.append(line)
    return "\n".join(out)


HERE = " <- here"
INDENT = int(KNOBS["short_indent"])
FIELD = int(KNOBS["short_field"])
HEAD = int(KNOBS["short_head"])
BOARD_WIDE = int(KNOBS["short_board_fields"])
ITEM_WIDE = int(KNOBS["short_item_fields"])
ROW_AT = re.compile(r"^\s{2,}(\d+)\s{2,}(\S.*)$")
SUM_AT = re.compile(r"^\s{2,}(\S.*?)\s{2,}(\d+\s+[\d,]+\s+\S+\s+\S+)$")
BOARD_HEADS = ("qty", "bought/u", "listed/u", "margin", "price", "profit")
ITEM_HEADS = ("rows", "units", "listed", "profit")


def columns(name, parts, room):
    return (f"{'':<{INDENT}}{name:<{room}}"
            + "".join(f"{one:>{FIELD}}" for one in parts))


def board_row(line):
    mark = HERE if line.endswith(HERE) else ""
    found = ROW_AT.match(line[:len(line) - len(mark)].rstrip())
    if found is None:
        return None
    parts = found.group(2).split()
    if len(parts) <= BOARD_WIDE:
        return None
    name = " ".join(parts[:len(parts) - BOARD_WIDE])
    return (f"{found.group(1):>{INDENT - 2}}  "
            + f"{name:<{HEAD}}"
            + "".join(f"{one:>{FIELD}}"
                      for one in parts[len(parts) - BOARD_WIDE:]) + mark)


def item_row(line):
    found = SUM_AT.match(line.rstrip())
    if found is None:
        return None
    return columns(found.group(1), found.group(2).split(),
                   HEAD + FIELD * (BOARD_WIDE - ITEM_WIDE))


TOTAL = re.compile(r"^\s{2,}(\S.*?)\s{2,}(-|[\d.]+[KMB]?)$")
FULL = INDENT + HEAD + FIELD * BOARD_WIDE


def total_row(line):
    found = TOTAL.match(line.rstrip())
    if found is None:
        return None
    return (f"{'':<{INDENT}}{found.group(1):<{FULL - INDENT - FIELD}}"
            f"{found.group(2):>{FIELD}}")


def aligned(text):
    out = []
    for line in text.splitlines():
        if "bought/u" in line and "row price" in line:
            out.append(columns("", BOARD_HEADS, HEAD))
            continue
        if line.strip().startswith("item ") and "rows" in line:
            out.append(columns("item", ITEM_HEADS,
                               HEAD + FIELD * (BOARD_WIDE - ITEM_WIDE)))
            continue
        done = board_row(line)
        if done is None:
            done = item_row(line)
        if done is None:
            done = total_row(line)
        out.append(done if done is not None else line)
    return "\n".join(out)


def caught(work):
    held = io.StringIO()
    keep = sys.stdout
    sys.stdout = held
    try:
        work()
    finally:
        sys.stdout = keep
    return held.getvalue()


def main():
    book = open_book()
    days = caught(lambda: (by_day(book), print(""), print(""),
                           report_day(book)))
    board = caught(report_board)
    market = caught(report_market)
    print(tighten(days))
    print("")
    print("")
    print(aligned(tighten(board, close=False)))
    print("")
    print("")
    print(tighten(market))


if __name__ == "__main__":
    main()
