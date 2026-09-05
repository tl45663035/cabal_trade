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
BOARD_ROW = re.compile(r"^\s{4,}(\d+)\s{2,}(.+?)\s+x([\d,]+)"
                       r"(?:\s+(?:[\d,]+|-|[-+]?[\d.]+%))*\s+([\d,]+)\s*$")
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
            board.append((index, found.group(2).strip(),
                          number(found.group(3)), number(found.group(4))))
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


def report(log, market, board, unread, balance, bought):
    print(f"NET WORTH -- from {log.name}")
    print("stock is valued at what each row is listed for; the market column "
          "is the price the run read at launch, for reference")
    print("")
    head = (f"{'row':>4}  {'item':<28}{'units':>8}{'listed/u':>12}"
            f"{'market':>12}{'value':>18}")
    print(head)
    print("-" * len(head))

    total = 0
    for index, name, qty, listed in board:
        units = qty * pack(name)
        each = listed // units if pack(name) > 1 else listed
        worth = listed if pack(name) > 1 else listed * qty
        at = market.get(key(name))
        total += worth
        print(f"{index:>4}  {name[:27]:<28}{units:>8,}{each:>12,}"
              f"{(f'{at:,}' if at is not None else '--'):>12}{worth:>18,}")

    if bought:
        print("")
        print("bought since that board was printed, so already paid for but "
              "not on it yet, in the bag or being listed, at the launch "
              "market price or what was spent:")
        for name, units, spent in bought:
            at = market.get(key(name))
            worth = units * at if at is not None else spent
            total += worth
            print(f"{'':>4}  {(name or '?')[:27]:<28}{units:>8,}{'':>12}"
                  f"{(f'{at:,}' if at is not None else '--'):>12}"
                  f"{worth:>18,}")

    print("-" * len(head))
    print(f"{'':>4}  {'stock':<28}{'':>8}{'':>12}{'':>12}{total:>18,}")
    if balance is None:
        print(f"{'':>4}  {'Alz':<28}{'':>8}{'':>12}{'':>12}{'unread':>18}")
    else:
        print(f"{'':>4}  {'Alz':<28}{'':>8}{'':>12}{'':>12}{balance:>18,}")
    print("=" * len(head))
    print(f"{'':>4}  {'NET WORTH':<28}{'':>8}{'':>12}{'':>12}"
          f"{total + (balance or 0):>18,}")

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
