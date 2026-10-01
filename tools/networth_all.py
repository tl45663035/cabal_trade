import datetime
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import networth_log as single
from profit_summary import SETTINGS, central_now

ROOT = Path(__file__).resolve().parent.parent
SUP = SETTINGS["supervise"]
ACCOUNTS = SUP["all_configs"]
FOLDER = ROOT / SUP["report_dir"]
NAME = SUP["all_dir"]
KEPT = single.LOGS / SUP["networth_history"].format(config=NAME)
READING = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s+([\d,]+)\s")
SAVED = re.compile(r"^(\w+),(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),(\d+)$")


def page(account, live, name=None):
    path = FOLDER / account / (name or SUP["networth_name"])
    if account == live and path.exists():
        return path.read_text(encoding="utf-8", errors="replace")
    out = subprocess.run(["git", "show", f"origin/{SUP['report_branch']}:"
                          f"{path.relative_to(ROOT).as_posix()}"],
                         cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=SUP["report_timeout"])
    return out.stdout if out.returncode == 0 else ""


def parse(text):
    readings, days = [], {}
    for line in text.splitlines():
        found = READING.match(line)
        if found:
            readings.append((datetime.datetime.strptime(found.group(1), single.STAMP),
                             single.number(found.group(2))))
            continue
        found = single.DAY_ROW.match(line)
        if found:
            day = datetime.datetime.strptime(found.group(1), single.DATE).date()
            days[day] = (single.number(found.group(2)), single.number(found.group(3)))
    return readings, days


def readings_of(text):
    out = []
    for line in text.splitlines():
        found = single.KEPT.match(line)
        if found:
            out.append((datetime.datetime.strptime(found.group(1), single.STAMP),
                        sum(int(part) for part in found.groups()[1:] if part)))
    return out


def recorded():
    held = {}
    if KEPT.exists():
        for line in KEPT.read_text(encoding="utf-8").splitlines():
            found = SAVED.match(line)
            if found:
                held[(found.group(1), datetime.datetime.strptime(found.group(2), single.STAMP))] = \
                    int(found.group(3))
    return held


def kept(held, keep_from):
    out = {}
    for account in ACCOUNTS:
        mine = sorted((when, total) for (who, when), total in held.items() if who == account)
        early = [row for row in mine if row[0].date() < keep_from][-1:]
        for when, total in early + [row for row in mine if row[0].date() >= keep_from]:
            out[(account, when)] = total
    return out


def grid(held, now):
    series = {a: sorted((when, total) for (who, when), total in held.items() if who == a)
              for a in ACCOUNTS}
    if not all(series.values()):
        return []
    moment = min(rows[0][0] for rows in series.values())
    at = {a: 0 for a in ACCOUNTS}
    out = []
    while moment <= now:
        values = []
        for a in ACCOUNTS:
            rows = series[a]
            while at[a] + 1 < len(rows) and rows[at[a] + 1][0] <= moment:
                at[a] += 1
            values.append(rows[at[a]][1])
        out.append((moment,) + tuple(values))
        moment += single.EVERY
    return out


def day_table(books, today):
    days = sorted({day for _, found in books.values() for day in found})
    names = ("net worth start", "net worth current", "change") + tuple(ACCOUNTS)
    labels = {day: f"{day:{single.DAY}}" + (single.SO_FAR if day == today else "") for day in days}
    room = max([len(label) for label in labels.values()] + [len("day")]) + single.GAP
    wide = [max(single.FIELD, len(name) + single.GAP) for name in names]
    head = f"{'day':<{room}}" + "".join(f"{n:>{w}}" for n, w in zip(names, wide))
    out = [f"NET WORTH BY DAY, ALL ACCOUNTS, LAST {single.DAYS} DAYS -- each account's own "
           f"start and current added up; the account columns are each one's change that day",
           "", head, "-" * len(head)]
    for day in days:
        held = [books[a][1].get(day) for a in ACCOUNTS]
        start = sum(h[0] for h in held if h)
        current = sum(h[1] for h in held if h)
        parts = "".join(f"{(f'{h[1] - h[0]:+,}' if h else '-'):>{w}}" for h, w in zip(held, wide[3:]))
        out.append(f"{labels[day]:<{room}}{start:>{wide[0]},}{current:>{wide[1]},}"
                   f"{current - start:>+{wide[2]},}{parts}")
    return out


def log_table(rows, today, latest):
    room = len(rows[0][0].strftime(single.STAMP)) + single.GAP
    head = (f"{'time':<{room}}{'net worth':>{single.FIELD}}{'since last':>{single.FIELD}}"
            f"{'since midnight':>{single.FIELD}}"
            + "".join(f"{a:>{single.FIELD}}" for a in ACCOUNTS))
    seen = ", ".join(f"{a} {latest[a]:{single.STAMP}}" for a in ACCOUNTS)
    out = [f"NET WORTH EVERY {single.EVERY // single.MINUTE} MINUTES, ALL ACCOUNTS, "
           f"{today:{single.DAY}} -- each account's latest reading at that time, added up; "
           f"latest readings: {seen}", "", head, "-" * len(head)]
    before, opened = None, single.worth(rows[0])
    for row in rows:
        total = single.worth(row)
        since = "-" if before is None else f"{total - before:+,}"
        out.append(f"{row[0]:{single.STAMP}}".ljust(room) + f"{total:>{single.FIELD},}"
                   f"{since:>{single.FIELD}}{total - opened:>+{single.FIELD},}"
                   + "".join(f"{v:>{single.FIELD},}" for v in row[1:]))
        before = total
    return out


def main():
    live = sys.argv[1] if len(sys.argv) > 1 else ACCOUNTS[0]
    now = central_now().replace(microsecond=0)
    today = now.date()
    keep_from = today - datetime.timedelta(days=single.DAYS - 1)
    books = {a: parse(page(a, live)) for a in ACCOUNTS}
    held = recorded()
    for account, (readings, _days) in books.items():
        pushed = readings_of(page(account, live, SUP["networth_history_report"]))
        for when, total in pushed + readings:
            if when <= now:
                held[(account, when)] = total
    held = kept(held, keep_from)
    single.staged_write(KEPT, "".join(f"{a},{when:{single.STAMP}},{total}\n"
                                      for (a, when), total in sorted(held.items(),
                                                                     key=lambda k: (k[0][1], k[0][0]))))
    every = grid(held, now)
    history = [row for row in every if row[0].date() >= keep_from]
    rows = [row for row in history if row[0].date() == today]
    folder = FOLDER / NAME
    folder.mkdir(parents=True, exist_ok=True)
    if not rows:
        print("not every account has a reading today yet; nothing written")
        return 0
    carried = [row for row in every if row[0].date() < keep_from][-1:]
    before = [row for row in every if row[0].date() < today][-1:]
    latest = {a: max(when for (who, when) in held if who == a) for a in ACCOUNTS}
    single.staged_write(folder / SUP["networth_name"],
                        "\n".join(day_table(books, today) + ["", ""]
                                  + log_table(rows, today, latest)) + "\n")
    single.staged_write(folder / SUP["networth_graph"],
                        single.graph(rows, history, (carried or [None])[0],
                                     (before or [None])[0], NAME, today))
    print(log_table(rows, today, latest)[-1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
