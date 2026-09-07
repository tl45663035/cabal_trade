import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TREE = ROOT / "src_1080p"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from profit_summary import PACK, pack


def key(name):
    return re.sub(r"[^a-z0-9]", "", PACK.sub(" ", name or "").lower())


MARKET_HEAD = re.compile(r"^market prices:\s*$")
MARKET_ROW = re.compile(r"^\s{2}(\S.*?)\s{2,}([\d,]+)\s*$")
BOARD_ROW = re.compile(r"^\s{4,}(\d+)\s{2,}(.+?)\s+x([\d,]+)\s+([\d,]+|-)"
                       r"\s+([\d,]+)\s+(?:[-+]?[\d.]+%|-)\s+([\d,]+)\s*$")
BOARD_UNREAD = re.compile(r"^\s{4,}(\d+)\s+UNREAD\s+(.*)$")
BOARD_HEAD = re.compile(r"^\s+board after pass \d+:")
BALANCE = re.compile(r"balance (?:after|before|now)\s+([\d,]+)")
RESUPPLY = re.compile(r'^TASK \{"kind": "resupply", "core": "([^"]+)"')
BOUGHT = re.compile(r"balance after\s+[\d,]+; spent ([\d,]+) bought \d+ pack\(s\) "
                    r"= ([\d,]+) core\(s\)")


def newest_log():
    logs = sorted(TREE.glob("logs/*_run.log"), key=lambda p: p.stat().st_mtime)
    return logs[-1] if logs else None


def number(text):
    return int(text.replace(",", ""))


def read(log):
    market, board, unread, balance = {}, [], [], None
    bought, core = [], None
    in_market = False
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        if MARKET_HEAD.match(line):
            in_market, market = True, {}
            continue
        if in_market:
            found = MARKET_ROW.match(line)
            if found:
                market[key(found.group(1))] = number(found.group(2))
                continue
            in_market = False

        if BOARD_HEAD.match(line):
            board, unread, bought = [], [], []
            continue

        found = BOARD_ROW.match(line)
        if found:
            index = int(found.group(1))
            if index == 1 and board:
                board, unread, bought = [], [], []
            cost = found.group(4)
            board.append((index, found.group(2).strip(),
                          number(found.group(3)), number(found.group(5)),
                          number(found.group(6)),
                          None if cost == "-" else number(cost)))
            continue

        found = BOARD_UNREAD.match(line)
        if found:
            unread.append((int(found.group(1)), found.group(2).strip()))
            continue

        found = RESUPPLY.match(line)
        if found:
            core = found.group(1)
            continue

        found = BALANCE.search(line)
        if found:
            balance = number(found.group(1))
            found = BOUGHT.search(line)
            if found:
                bought.append((core, number(found.group(2)),
                               number(found.group(1))))
    return market, board, unread, balance, bought


def row_worth(qty, each, listed):
    return listed * qty if listed == each and qty > 1 else listed


def row_profit(name, qty, each, cost):
    return None if cost is None else (each - cost) * qty * pack(name)


def profit_if_sold(board):
    rows = [(index, name, qty * pack(name), row_profit(name, qty, each, cost))
            for index, name, qty, each, _, cost in board]
    total = sum(gain for *_, gain in rows if gain is not None)
    unknown = sum(1 for *_, gain in rows if gain is None)
    return rows, total, unknown


def bought_worth(market, bought):
    out = []
    for name, units, spent in bought:
        at = market.get(key(name))
        out.append((name, units, at, units * at if at is not None else spent))
    return out


def summary(log, indent="    ", width=40, number=18, extra=0):
    market, board, unread, balance, bought = read(log)
    if not board:
        return
    stock = sum(row_worth(qty, each, listed)
                for _, _, qty, each, listed, _ in board)
    held = sum(worth for *_, worth in bought_worth(market, bought))
    _, total, unknown = profit_if_sold(board)
    print(f"{indent}{'stock at its listed price':<{width}}{stock:>{number},}")
    if held:
        print(f"{indent}{'bought since that board, not on it yet':<{width}}"
              f"{held:>{number},}")
    if unread:
        print(f"{indent}{f'{len(unread)} row(s) unread, worth nothing here':<{width}}"
              f"{0:>{number},}")
    print(f"{indent}{'Alz, latest balance line':<{width}}"
          f"{(f'{balance:,}' if balance is not None else 'unread'):>{number}}")
    print(f"{indent}{'NET WORTH':<{width}}{stock + held + (balance or 0):>{number},}")
    print(f"{indent}{'PROFIT IF SOLD, every row at its listed price':<{width}}"
          f"{'':>{number}}{total:>{extra},}")
    if unknown:
        print(f"{indent}{unknown} row(s) show no bought price and are not counted")


def report(log, market, board, unread, balance, bought):
    print(f"NET WORTH -- from {log.name}")
    print("stock is valued at what each row is listed for; the market column "
          "is the price the run read at launch, for reference")
    print("")
    head = (f"{'row':>4}  {'item':<28}{'units':>8}{'bought/u':>12}{'listed/u':>12}"
            f"{'market':>12}{'value':>18}{'profit if sold':>16}")
    print(head)
    print("-" * len(head))

    total = 0
    for index, name, qty, each, listed, cost in board:
        units = qty * pack(name)
        worth = row_worth(qty, each, listed)
        at = market.get(key(name))
        gain = row_profit(name, qty, each, cost)
        total += worth
        print(f"{index:>4}  {name[:27]:<28}{units:>8,}"
              f"{(f'{cost:,}' if cost is not None else '-'):>12}{each:>12,}"
              f"{(f'{at:,}' if at is not None else '--'):>12}{worth:>18,}"
              f"{(f'{gain:,}' if gain is not None else '-'):>16}")

    if bought:
        print("")
        print("bought since that board was printed, so already paid for but "
              "not on it yet, in the bag or being listed, at the launch "
              "market price or what was spent:")
        for name, units, at, worth in bought_worth(market, bought):
            total += worth
            print(f"{'':>4}  {(name or '?')[:27]:<28}{units:>8,}{'':>12}{'':>12}"
                  f"{(f'{at:,}' if at is not None else '--'):>12}"
                  f"{worth:>18,}")

    _, gain, unknown = profit_if_sold(board)
    pad = f"{'':>4}  {'':<28}{'':>8}{'':>12}{'':>12}{'':>12}"
    print("-" * len(head))
    print(f"{'':>4}  {'stock':<28}{'':>8}{'':>12}{'':>12}{'':>12}{total:>18,}"
          f"{gain:>16,}")
    if balance is None:
        print(f"{pad}{'unread':>18}")
    else:
        print(f"{'':>4}  {'Alz':<28}{'':>8}{'':>12}{'':>12}{'':>12}{balance:>18,}")
    print("=" * len(head))
    print(f"{'':>4}  {'NET WORTH':<28}{'':>8}{'':>12}{'':>12}{'':>12}"
          f"{total + (balance or 0):>18,}")
    print(f"{'':>4}  {'PROFIT IF SOLD':<28}{'':>8}{'':>12}{'':>12}{'':>12}"
          f"{'':>18}{gain:>16,}")
    if unknown:
        print(f"{unknown} row(s) show no bought price, so their profit is not counted")

    if unread:
        print("")
        print("rows the run could not read, worth nothing here:")
        for index, text in unread:
            print(f"  {index:>3}  {text}")
    if balance is None:
        print("")
        print("no balance line in this log, so the Alz is missing from the "
              "total -- a run that has not bought anything never prints one")


def networth():
    log = newest_log()
    if log is None:
        print("  no run log to read")
        return False
    market, board, unread, balance, bought = read(log)
    if not board:
        print(f"  {log.name} has no row table yet; the run prints one once it "
              f"has seeded the board")
        return False
    report(log, market, board, unread, balance, bought)
    return True


if __name__ == "__main__":
    sys.exit(0 if networth() else 1)
