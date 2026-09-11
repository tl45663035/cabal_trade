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


FIELDS = ("sold", "revenue", "sold_cost", "held", "expected", "held_cost",
          "gone", "gone_value", "gone_cost")


def load(start):
    if not LEDGER.exists():
        raise SystemExit(f"no ledger at {LEDGER}")
    conn = sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(purchases)")}
    expect = "expect" if "expect" in columns else "NULL"
    buys, sells = [], []
    for at, run, item, spend, qty, exp in conn.execute(
            f"SELECT at, run, item, spend, qty, {expect} FROM purchases "
            f"WHERE at>=? ORDER BY at, id", (start,)):
        if qty:
            buys.append((at, run, item, spend or 0, qty, exp))
    for at, run, item, qty, price, proceeds in conn.execute(
            "SELECT at, run, item, qty, price, proceeds FROM sales "
            "WHERE at>=? ORDER BY at, id", (start,)):
        if qty:
            gross = proceeds if proceeds is not None else (price or 0) * qty
            sells.append((at, run, item, units_sold(item, qty), gross))
    conn.close()
    return buys, sells


def sale_prices(sells):
    seen = collections.defaultdict(list)
    for at, _run, item, units, gross in sells:
        if units and gross:
            seen[key(item)].append(gross / units)
            seen[(at[:10], key(item))].append(gross / units)
    return {k: sorted(v)[len(v) // 2] for k, v in seen.items() if v}


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


def boards(since):
    import networth
    snaps = []
    for log in LOGS.glob("*_run.log"):
        launched = log_launch(log)
        if launched is None or launched < since:
            continue
        try:
            text = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        budget = None
        table = None
        for line in text.splitlines():
            found = networth.BOARD_ROW.match(line)
            if found:
                if table is None or int(found.group(1)) == 1:
                    table = collections.Counter()
                name = found.group(2).strip()
                table[key(name)] += networth.number(found.group(3)) * pack(name)
                continue
            found = BUDGET.match(line)
            if found:
                budget = float(found.group(1))
                if table is not None:
                    snaps.append((launched, table))
                    table = None
                continue
            found = PASS_LEFT.match(line)
            if found and table is not None and budget is not None:
                when = launched + datetime.timedelta(
                    minutes=budget - float(found.group(1)))
                snaps.append((when, table))
                table = None
    return sorted(snaps, key=lambda snap: snap[0])


def checkpoints(snaps, first_midnight, on_board):
    checks = []
    midnight = first_midnight
    today = datetime.datetime.combine(datetime.date.today(),
                                      datetime.time.min)
    while midnight <= today:
        before = [snap for snap in snaps if snap[0] <= midnight]
        if before:
            when, table = before[-1]
            if not checks or checks[-1][0] != stamp(when):
                checks.append((stamp(when), table))
        midnight += datetime.timedelta(days=1)
    if on_board is not None:
        checks.append((stamp(datetime.datetime.now()), on_board))
    return checks


def close_book(buys, sells, fallback, listed, checks):
    lots = []
    open_lots = collections.defaultdict(collections.deque)
    unmatched = []
    guessed = 0
    buys = collections.deque(sorted(buys))

    def stock_up(until):
        nonlocal guessed
        while buys and (until is None or buys[0][0] <= until):
            at, run, item, spend, qty, expect = buys.popleft()
            k = key(item)
            if not expect:
                expect = fallback.get(k)
                guessed += qty
            cost = spend / qty
            lot = {"at": at, "run": run, "k": k, "item": item,
                   "bucket": bucket(item), "qty": qty, "cost": cost,
                   "expect": expect or cost, "sold": 0, "revenue": 0.0,
                   "gone": 0, "gone_value": 0.0, "left": qty}
            lots.append(lot)
            open_lots[k].append(lot)

    def reconcile(at, on_board):
        for k, queue in open_lots.items():
            over = sum(lot["left"] for lot in queue) - on_board.get(k, 0)
            while over > 0 and queue:
                lot = queue[0]
                take = min(over, lot["left"])
                at_price = fallback.get((at[:10], k),
                                        fallback.get(k, lot["expect"]))
                lot["gone"] += take
                lot["gone_value"] += take * at_price
                lot["left"] -= take
                over -= take
                if lot["left"] <= 0:
                    queue.popleft()

    events = [(sale[0], 1, sale) for sale in sells]
    events += [(at, 0, table) for at, table in checks]
    for at, kind, event in sorted(events, key=lambda e: (e[0], e[1])):
        stock_up(at)
        if kind == 0:
            reconcile(at, event)
            continue
        _at, run, item, n, gross = event
        if not gross:
            continue
        k = key(item)
        each = gross / n
        left = n
        while left and open_lots[k]:
            lot = open_lots[k][0]
            take = min(left, lot["left"])
            lot["sold"] += take
            lot["revenue"] += take * each
            lot["left"] -= take
            left -= take
            if lot["left"] <= 0:
                open_lots[k].popleft()
        if left:
            unmatched.append((at, k, left))
    stock_up(None)

    for lot in lots:
        lot["priced"] = "listed" if lot["k"] in listed else "expect"
        lot["value"] = listed.get(lot["k"], lot["expect"])
    return lots, unmatched, guessed


def totals(lots):
    t = {f: 0 for f in FIELDS}
    for lot in lots:
        t["sold"] += lot["sold"]
        t["revenue"] += lot["revenue"]
        t["sold_cost"] += lot["sold"] * lot["cost"]
        t["held"] += lot["left"]
        t["expected"] += lot["left"] * lot["value"]
        t["held_cost"] += lot["left"] * lot["cost"]
        t["gone"] += lot["gone"]
        t["gone_value"] += lot["gone_value"]
        t["gone_cost"] += lot["gone"] * lot["cost"]
    return t


def units(t):
    return t["sold"] + t["held"] + t["gone"]


def realised(t):
    return t["revenue"] - t["sold_cost"]


def assumed(t):
    return t["expected"] - t["held_cost"] + t["gone_value"] - t["gone_cost"]


def profit(t):
    return realised(t) + assumed(t)


def margin(t):
    gross = t["revenue"] + t["expected"] + t["gone_value"]
    return f"{100 * profit(t) / gross:>7.1f}%" if gross else f"{'--':>8}"


def within(lots, begin, end=None):
    start = stamp(begin)
    stop = None if end is None else stamp(end)
    return [lot for lot in lots
            if lot["at"] >= start and (stop is None or lot["at"] < stop)]


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


def open_book(count=DAYS_BACK):
    today = datetime.date.today()
    first = today - datetime.timedelta(days=count - 1)
    since = datetime.datetime.combine(first, datetime.time.min)
    opened = since - datetime.timedelta(days=count)
    buys, sells = load(stamp(opened))
    listed, on_board, log = listed_now()
    checks = checkpoints(boards(opened - datetime.timedelta(days=1)),
                         opened + datetime.timedelta(days=1), on_board)
    lots, unmatched, guessed = close_book(buys, sells, sale_prices(sells),
                                         listed, checks)
    return {"first": first, "today": today, "lots": lots,
            "unmatched": unmatched, "guessed": guessed, "listed": listed,
            "log": log}


def by_day(book):
    count = DAYS_BACK
    first, today = book["first"], book["today"]
    print(f"LAST {count} DAYS -- {first:%Y-%m-%d} to {today:%Y-%m-%d}, each "
          f"day midnight to midnight")
    print("a lot counts on the day it was BOUGHT; a sale is matched "
          "oldest-lot-first whichever run sold it; stock still on the board "
          "is valued at the price it is listed at")
    print("at each midnight and now, stock the book holds beyond what the "
          "board showed left without a booked sale; it closes at that day's "
          "median sale price")
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
        t = collections.Counter(totals(within(book["lots"], begin, end)))
        grand.update(t)
        up = up_hours(spans, begin, end)
        all_up += up
        label = f"{day:%a %Y-%m-%d}" + (" (so far)" if not back else "")
        print(f"{label:<26}{up:>7.2f}h{profit(t):>15,.0f}"
              f"{realised(t):>15,.0f}{assumed(t):>15,.0f}"
              f"{units(t):>8,}{margin(t)}"
              f"{profit(t) / up if up else 0:>14,.0f}")
    line("=", width=118)
    print(f"{f'{count} DAYS':<26}{all_up:>7.2f}h{profit(grand):>15,.0f}"
          f"{realised(grand):>15,.0f}{assumed(grand):>15,.0f}"
          f"{units(grand):>8,}{margin(grand)}"
          f"{profit(grand) / all_up if all_up else 0:>14,.0f}")


def report_day(book):
    start = datetime.datetime.now().replace(hour=0, minute=0, second=0,
                                            microsecond=0)
    now = datetime.datetime.now().strftime("%H:%M")
    print(f"PROFIT SUMMARY -- stock bought since {start:%Y-%m-%d} 00:00 "
          f"(as of {now})")
    print("realised = sold, at what the collection paid, whichever run sold "
          "it; assumed = still on the board at its listed price, plus stock "
          "that left the board with no booked sale, at its day's median sale "
          "price")
    print("")
    lots = within(book["lots"], start)
    if not lots:
        print("  nothing has been bought today.")
        return

    items = {}
    for lot in lots:
        acc = items.setdefault(lot["k"], {"bucket": lot["bucket"], "lots": []})
        acc["lots"].append(lot)
    rows = {k: dict(totals(v["lots"]), bucket=v["bucket"])
            for k, v in items.items()}

    print(f"{'item':<26}{'profit':>15}{'realised':>15}{'assumed':>15}"
          f"{'units':>8}{'margin':>8}{'cost':>16}")
    line(width=103)
    groups = {"Cores": collections.Counter(), "Chaos": collections.Counter()}
    for k, r in sorted(rows.items(),
                       key=lambda kv: -(kv[1]["revenue"] + kv[1]["expected"])):
        cost = r["sold_cost"] + r["held_cost"] + r["gone_cost"]
        print(f"{k[:25]:<26}{profit(r):>15,.0f}{realised(r):>15,.0f}"
              f"{assumed(r):>15,.0f}{units(r):>8,}{margin(r)}"
              f"{cost:>16,.0f}")
        groups[r["bucket"]].update({f: r[f] for f in FIELDS})
    line(width=103)
    total = collections.Counter()
    for label in ("Cores", "Chaos"):
        g = groups[label]
        total.update(g)
        if not units(g):
            continue
        print(f"{label:<26}{profit(g):>15,.0f}{realised(g):>15,.0f}"
              f"{assumed(g):>15,.0f}{units(g):>8,}{margin(g)}"
              f"{g['sold_cost'] + g['held_cost'] + g['gone_cost']:>16,.0f}")
    line("=", width=103)
    spent = total["sold_cost"] + total["held_cost"] + total["gone_cost"]
    print(f"{'TOTAL':<26}{profit(total):>15,.0f}{realised(total):>15,.0f}"
          f"{assumed(total):>15,.0f}{units(total):>8,}"
          f"{margin(total)}{spent:>16,.0f}")

    print("")
    print("by run, on the stock each run bought:")
    print(f"  {'launched':<21}{'units':>7}{'realised':>15}{'assumed':>15}"
          f"{'profit':>15}{'hours':>7}{'an hour':>15}")
    runs = sorted({lot["run"] for lot in lots if lot["run"]})
    all_hours = 0.0
    for run in runs:
        t = totals([lot for lot in lots if lot["run"] == run])
        ran = run_hours(run)
        all_hours += ran
        rate = f"{profit(t) / ran:>15,.0f}" if ran else f"{'--':>15}"
        tag = "  live" if run_is_live(run) else ""
        print(f"  {run:<21}{units(t):>7,}{realised(t):>15,.0f}"
              f"{assumed(t):>15,.0f}{profit(t):>15,.0f}{ran:>7.2f}{rate}{tag}")
    line(width=103)
    print(f"  {len(runs)} run(s) trading for {all_hours:.2f} hour(s)"
          f"{'':>40}{profit(total) / all_hours if all_hours else 0:>15,.0f} an hour")
    print("  hours are launch to last trade, so a run still going is short by "
          "whatever it has not traded in yet")

    unmatched = collections.Counter()
    for at, k, n in book["unmatched"]:
        if at >= stamp(start):
            unmatched[k] += n
    if unmatched:
        print("")
        print("sold today but matched to no lot the script bought:")
        for k, n in unmatched.most_common():
            print(f"  {k:<26}{n:>8,} units")

    gone = {k: r for k, r in rows.items() if r["gone"]}
    if gone:
        print("")
        print("bought today, off the board with no sale in the ledger; "
              "closed at the median sale price of the item on the day it "
              "left:")
        for k, r in sorted(gone.items(), key=lambda kv: -kv[1]["gone_cost"]):
            print(f"  {k:<26}{r['gone']:>8,} units  cost {r['gone_cost']:>15,.0f}"
                  f"  closed {r['gone_value']:>15,.0f}  "
                  f"({r['gone_value'] - r['gone_cost']:>+13,.0f})")

    held = {k: r for k, r in rows.items() if r["held"]}
    if held:
        print("")
        print("still on the board from today's stock, counted above at the "
              "price it is listed at now:")
        for k, r in sorted(held.items(), key=lambda kv: -kv[1]["held_cost"]):
            priced = {lot["priced"] for lot in items[k]["lots"] if lot["left"]}
            note = ("" if priced == {"listed"} else
                    "  (not on the board; at the price it was bought against)"
                    if priced == {"expect"} else
                    "  (partly off the board, that part at the price it was "
                    "bought against)")
            print(f"  {k:<26}{r['held']:>8,} units  cost {r['held_cost']:>15,.0f}"
                  f"  listed {r['expected']:>15,.0f}  "
                  f"({r['expected'] - r['held_cost']:>+13,.0f}){note}")

    if book["guessed"]:
        print("")
        print(f"{book['guessed']:,} unit(s) were bought before the ledger "
              f"recorded the price they were bought against; unsold ones off "
              f"the board are valued at the median sale price for the item, "
              f"or at cost if it never sold")

    print("")
    print("revenue is what the collections actually paid; the shop's sales "
          "fee is 0.0%, so that is the full sale price")


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
    print(f"MARKET -- {log.name}, pass {number_of_pass}, log last written {written:%H:%M}"
          f"{'' if any('ran for' in l for l in lines[-40:]) else ' (live)'}{stale}")
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
    book = open_book()
    by_day(book)
    print("")
    print("")
    report_day(book)
    print("")
    print("")
    report_board()
    print("")
    print("")
    report_market()


if __name__ == "__main__":
    main()
