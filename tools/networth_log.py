import datetime
import math
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import networth
from profit_summary import KNOBS, SETTINGS, central_now

STAMP = "%Y-%m-%d %H:%M:%S"
DATE = "%Y-%m-%d"
DAY = "%a %Y-%m-%d"
TITLE_DAY = "%a %d %b %Y"
WEEK_DAY = "%a %d %b"
TODAY_DAY = "%d %b"
CLOCK = "%H:%M"
SO_FAR = " (so far)"
DOT = "\u00b7"
FIELD = int(KNOBS["networth_field"])
GAP = int(KNOBS["short_gap"])
DAYS = int(KNOBS["profit_days_back"])
LOOK = KNOBS["networth_graph"]
WATCH = SETTINGS["supervise"]
EVERY = datetime.timedelta(seconds=int(WATCH["networth_every"]))
MINUTE = datetime.timedelta(minutes=1)
WHOLE_DAY = datetime.timedelta(days=1)
HOURS = WHOLE_DAY // datetime.timedelta(hours=1)
LOGS = networth.TREE / "logs"
AFTER = re.compile(r"^  board after pass (\d+):$", re.M)
PASS_END = re.compile(r"^  pass \d+: ", re.M)
SIGNED = r"(?:[-+][\d,]+|-)"
DAY_ROW = re.compile(rf"^\w+ (\d{{4}}-\d\d-\d\d)(?:{re.escape(SO_FAR)})?\s+([\d,]+)"
                     rf"\s+([\d,]+)\s+{SIGNED}\s*$")
KEPT = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),(\d+),(\d+)$")


def number(text):
    return int(text.replace(",", ""))


def reading():
    log = networth.newest_log()
    if log is None:
        return None
    text = log.read_text(encoding="utf-8", errors="replace")
    heads = list(AFTER.finditer(text))
    if not heads:
        return None
    section = text[heads[-1].end():]
    end = PASS_END.search(section)
    section = section[:end.start()] if end else section
    first, last = networth.counted_rows(text)
    stock, balance, seen = 0, None, False
    for line in section.splitlines():
        found = networth.BOARD_ROW.match(line)
        if found:
            if first <= int(found.group(1)) <= last:
                stock += networth.row_worth(number(found.group(3)),
                                            number(found.group(5)),
                                            number(found.group(6)))
                seen = True
            continue
        found = networth.BALANCE.search(line)
        if found:
            balance = number(found.group(1))
    if not seen or balance is None:
        return None
    return central_now().replace(microsecond=0), balance, stock


def recorded(path):
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        found = KEPT.match(line)
        if found:
            out.append((datetime.datetime.strptime(found.group(1), STAMP),
                        int(found.group(2)), int(found.group(3))))
    return out


def saved_days(path):
    days = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            found = DAY_ROW.match(line)
            if found:
                day = datetime.datetime.strptime(found.group(1), DATE).date()
                days[day] = [number(found.group(2)), number(found.group(3))]
    return days


def fold(days, rows):
    for when, alz, stock in rows:
        held = days.setdefault(when.date(), [alz + stock, alz + stock])
        held[1] = alz + stock
    return days


def day_table(days, today):
    labels = {day: f"{day:{DAY}}" + (SO_FAR if day == today else "")
              for day in days}
    room = max(len(label) for label in labels.values()) + GAP
    names = ("net worth start", "net worth current", "change")
    wide = [max(FIELD, len(name) + GAP) for name in names]
    head = f"{'day':<{room}}" + "".join(f"{n:>{w}}" for n, w in zip(names, wide))
    out = [f"NET WORTH BY DAY, LAST {DAYS} DAYS -- start is the first reading "
           f"after midnight, current is the latest reading of that day, "
           f"midnight to midnight Central", "", head, "-" * len(head)]
    for day in sorted(days):
        start, current = days[day]
        out.append(f"{labels[day]:<{room}}{start:>{wide[0]},}"
                   f"{current:>{wide[1]},}{current - start:>+{wide[2]},}")
    return out


def log_table(rows, today):
    room = len(rows[0][0].strftime(STAMP)) + GAP
    head = (f"{'time':<{room}}{'net worth':>{FIELD}}"
            f"{'since last':>{FIELD}}{'since midnight':>{FIELD}}"
            f"{'Alz':>{FIELD}}{'stock':>{FIELD}}")
    out = [f"NET WORTH EVERY {EVERY // MINUTE} MINUTES, {today:{DAY}} -- Alz "
           f"plus stock at its listed price, from the board and balance the run "
           f"printed at the end of its latest pass", "", head, "-" * len(head)]
    before, opened = None, rows[0][1] + rows[0][2]
    for when, alz, stock in rows:
        total = alz + stock
        since = "-" if before is None else f"{total - before:+,}"
        out.append(f"{when:{STAMP}}".ljust(room) +
                   f"{total:>{FIELD},}{since:>{FIELD}}"
                   f"{total - opened:>+{FIELD},}"
                   f"{alz:>{FIELD},}{stock:>{FIELD},}")
        before = total
    return out


def tone(name):
    return LOOK["colors"][name]


def nice(span):
    raw = span / int(LOOK["ticks"])
    mag = 10 ** math.floor(math.log10(raw))
    return next(step * mag for step in LOOK["steps"] if raw <= step * mag)


def changes(rows):
    start = rows[0][1] + rows[0][2]
    return [(when, alz + stock - start) for when, alz, stock in rows]


def scale(values, top, bottom):
    low, high = min(values + [0]), max(values + [0])
    step = nice((high - low) or LOOK["flat_span"])
    y_max = math.ceil(high / step) * step or step
    y_min = math.floor(low / step) * step
    y = lambda v: top + (y_max - v) / (y_max - y_min) * (bottom - top)
    return y, [y_min + k * step for k in range(round((y_max - y_min) / step) + 1)]


def signed_colour(value):
    return tone("gain") if value >= 0 else tone("loss")


def strong(body):
    return f'<tspan font-weight="700" fill="{tone("ink")}">{body}</tspan>'


def header(width, eyebrow, title, detail, big, big_colour, note):
    pad, small = LOOK["pad"], LOOK["small"]
    return [f'<rect width="{width}" height="{LOOK["card_height"]}" rx="{LOOK["radius"]}" '
            f'fill="{tone("card")}" stroke="{tone("edge")}"/>',
            f'<text x="{pad}" y="{LOOK["eyebrow_y"]}" font-size="{small}" font-weight="700" '
            f'letter-spacing="{LOOK["eyebrow_spacing"]}" fill="{tone("accent")}">{eyebrow}</text>',
            f'<text x="{pad}" y="{LOOK["title_y"]}" font-size="{LOOK["title_font"]}" '
            f'font-weight="700" fill="{tone("ink")}">{title}</text>',
            f'<text x="{pad}" y="{LOOK["detail_y"]}" fill="{tone("muted")}">{detail}</text>',
            f'<text x="{width - pad}" y="{LOOK["big_y"]}" font-size="{LOOK["big_font"]}" '
            f'font-weight="700" text-anchor="end" fill="{big_colour}">{big}</text>',
            f'<text x="{width - pad}" y="{LOOK["big_note_y"]}" font-size="{small}" '
            f'text-anchor="end" fill="{tone("muted")}">{note}</text>']


def grid(y, ticks, left, right, base_label):
    out = []
    for v in ticks:
        base = v == 0
        line = (f'stroke="{tone("base")}" stroke-width="{LOOK["base_width"]}"' if base else
                f'stroke="{tone("grid")}" stroke-width="1" stroke-dasharray="{LOOK["dash"]}"')
        out.append(f'<line x1="{left}" x2="{right}" y1="{y(v):.1f}" y2="{y(v):.1f}" {line}/>')
        out.append(f'<text x="{left - LOOK["label_pad"]}" y="{y(v):.1f}" text-anchor="end" '
                   f'dominant-baseline="middle" font-weight="{700 if base else 400}" '
                   f'fill="{tone("ink") if base else tone("muted")}">'
                   f'{base_label if base else f"{v:+,.0f}"}</text>')
    return out


def hour_marks(x, origin, every, top, bottom, guides=0):
    out = []
    for hour in range(0, HOURS + 1, every):
        hx = x(origin + datetime.timedelta(hours=hour))
        if guides and hour % guides == 0 and 0 < hour < HOURS:
            out.append(f'<line x1="{hx:.1f}" x2="{hx:.1f}" y1="{top}" y2="{bottom}" '
                       f'stroke="{tone("grid")}" stroke-width="1"/>')
        out.append(f'<line x1="{hx:.1f}" x2="{hx:.1f}" y1="{bottom}" '
                   f'y2="{bottom + LOOK["tick"]}" stroke="{tone("muted")}" stroke-width="1"/>')
        if hour < HOURS:
            out.append(f'<text x="{hx:.1f}" y="{bottom + LOOK["hour_label_y"]}" '
                       f'font-size="{LOOK["small"]}" text-anchor="middle" '
                       f'fill="{tone("muted")}">{hour:02d}:00</text>')
    return out


def area(x, y, points, left, right, top, bottom, tag):
    base = y(0)
    line = "L".join(f"{x(when):.1f} {y(v):.1f}" for when, v in points)
    shape = f"M{x(points[0][0]):.1f} {base:.1f}L{line}L{x(points[-1][0]):.1f} {base:.1f}Z"
    out = [f'<defs><path id="shape-{tag}" d="{shape}"/><path id="line-{tag}" d="M{line}"/>'
           f'<clipPath id="up-{tag}"><rect x="{left}" y="{top}" width="{right - left}" '
           f'height="{max(0.0, base - top):.1f}"/></clipPath>'
           f'<clipPath id="down-{tag}"><rect x="{left}" y="{base:.1f}" width="{right - left}" '
           f'height="{max(0.0, bottom - base):.1f}"/></clipPath></defs>']
    for side, colour in (("up", tone("gain")), ("down", tone("loss"))):
        out.append(f'<use href="#shape-{tag}" fill="{colour}" '
                   f'fill-opacity="{LOOK["area_opacity"]}" clip-path="url(#{side}-{tag})"/>')
        out.append(f'<use href="#line-{tag}" fill="none" stroke="{colour}" '
                   f'stroke-width="{LOOK["line"]}" stroke-linejoin="round" '
                   f'stroke-linecap="round" clip-path="url(#{side}-{tag})"/>')
    return out


def dot(cx, cy, colour):
    return (f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{LOOK["dot"]}" fill="{colour}" '
            f'stroke="{tone("card")}" stroke-width="{LOOK["dot_ring"]}"/>')


def end_label(cx, cy, rising, lines, right, top, bottom):
    look = LOOK["end"]
    gap, step = look["gap"], look["line"]
    width = max(len(body) * size for body, size, _, _ in lines) * LOOK["char_w"]
    height = step * (len(lines) - 1)
    if cx + gap + width <= right:
        anchor, lx, first = "start", cx + gap, cy - height / 2 + look["lift"]
    else:
        anchor, lx = "end", cx - gap
        first = cy - gap - height if rising else cy + gap + lines[0][1]
    first = min(max(first, top + lines[0][1]), bottom - height - look["lift"])
    return [f'<text x="{lx:.1f}" y="{first + step * k:.1f}" text-anchor="{anchor}" '
            f'font-size="{size}" font-weight="{weight}" fill="{fill}" stroke="{tone("card")}" '
            f'stroke-width="{look["halo"]}" stroke-linejoin="round" paint-order="stroke">'
            f'{body}</text>' for k, (body, size, weight, fill) in enumerate(lines)]


def today_card(rows, name, width):
    left, right = LOOK["pad"] + LOOK["axis_room"], width - LOOK["pad"]
    top, bottom = LOOK["today"]["plot_top"], LOOK["card_height"] - LOOK["plot_bottom"]
    when, alz, stock = rows[-1]
    origin = datetime.datetime.combine(when.date(), datetime.time())
    x = lambda moment: left + (moment - origin) / WHOLE_DAY * (right - left)
    points = changes(rows)
    y, ticks = scale([v for _, v in points], top, bottom)
    began = rows[0][0]
    start, now, change = rows[0][1] + rows[0][2], alz + stock, points[-1][1]
    colour = signed_colour(change)
    out = header(width, f"{name.upper()} {DOT} NET WORTH TODAY", "Since midnight",
                 f"{when:{TITLE_DAY}} {DOT} started at {strong(f'{start:,}')} at "
                 f"{began:{CLOCK}} {DOT} now {strong(f'{now:,}')} at {when:{CLOCK}} Central",
                 f"{change:+,}", colour, f"Alz since {began:{CLOCK}}")
    out += grid(y, ticks, left, right, f"{start:,}")
    out += hour_marks(x, origin, LOOK["today"]["hours"], top, bottom, LOOK["today"]["guides"])
    out += area(x, y, points, left, right, top, bottom, "today")
    ex, ey = x(when), y(change)
    earlier = [v for moment, v in points if when - moment >= LOOK["end"]["slope"] * MINUTE]
    rising = not earlier or earlier[-1] <= change
    out.append(dot(ex, ey, colour))
    out += end_label(ex, ey, rising,
                     [(f"{change:+,} Alz", LOOK["end"]["font"], 700, colour),
                      (f"net worth {now:,}", LOOK["font"], 600, tone("ink"))],
                     right, top, bottom)
    return out


def week_card(history, name, today, width):
    look = LOOK["week"]
    left, right = LOOK["pad"] + LOOK["axis_room"], width - LOOK["pad"]
    top, bottom = look["plot_top"], LOOK["card_height"] - LOOK["plot_bottom"]
    first = today - datetime.timedelta(days=DAYS - 1)
    origin = datetime.datetime.combine(first, datetime.time())
    x = lambda moment: left + (moment - origin) / (WHOLE_DAY * DAYS) * (right - left)
    days = {}
    for row in history:
        days.setdefault(row[0].date(), []).append(row)
    series = {day: changes(rows) for day, rows in days.items()}
    y, ticks = scale([v for points in series.values() for _, v in points], top, bottom)
    total = sum(points[-1][1] for points in series.values())
    out = header(width, f"{name.upper()} {DOT} NET WORTH BY DAY", f"Last {DAYS} days",
                 "each day starts again at its first reading after midnight",
                 f"{total:+,}", signed_colour(total),
                 f"Alz, the {len(series)} daily changes added up")
    band_top = look["day_label_y"] - LOOK["font"] - LOOK["label_pad"]
    for k in range(1, DAYS, 2):
        midnight = origin + WHOLE_DAY * k
        out.append(f'<rect x="{x(midnight):.1f}" y="{band_top}" '
                   f'width="{x(midnight + WHOLE_DAY) - x(midnight):.1f}" '
                   f'height="{bottom - band_top}" fill="{tone("stripe")}"/>')
    out += grid(y, ticks, left, right, "day start")
    for k in range(DAYS):
        day = first + datetime.timedelta(days=k)
        midnight = origin + WHOLE_DAY * k
        sx = x(midnight) + LOOK["label_pad"]
        label = f"Today {day:{TODAY_DAY}}" if day == today else f"{day:{WEEK_DAY}}"
        out.append(f'<text x="{sx:.1f}" y="{look["day_label_y"]}" font-weight="700" '
                   f'fill="{tone("accent") if day == today else tone("ink")}">{label}</text>')
        out += hour_marks(x, midnight, look["hours"], top, bottom)
        if day in series:
            points = series[day][::look["every"]]
            if points[-1] != series[day][-1]:
                points.append(series[day][-1])
            colour = signed_colour(points[-1][1])
            out.append(f'<text x="{sx:.1f}" y="{look["change_y"]}" font-weight="700" '
                       f'fill="{colour}">{points[-1][1]:+,}</text>')
            out += area(x, y, points, left, right, top, bottom, f"day{k}")
            out.append(dot(x(points[-1][0]), y(points[-1][1]), colour))
    return out


def graph(rows, history, name, today):
    margin, gap, card = LOOK["margin"], LOOK["gap"], LOOK["card_height"]
    width = LOOK["width"]
    inner = width - 2 * margin
    total = 2 * margin + 2 * card + gap
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{total}" '
           f'viewBox="0 0 {width} {total}" font-family="{LOOK["face"]}" '
           f'font-size="{LOOK["font"]}" style="font-variant-numeric: tabular-nums">',
           f'<rect width="{width}" height="{total}" fill="{tone("page")}"/>',
           f'<g transform="translate({margin},{margin})">']
    out += today_card(rows, name, inner)
    out += ["</g>", f'<g transform="translate({margin},{margin + card + gap})">']
    out += week_card(history, name, today, inner)
    out += ["</g>", "</svg>"]
    return "\n".join(out) + "\n"


def staged_write(path, text):
    staged = path.with_name(f"{path.name}.tmp")
    staged.write_text(text, encoding="utf-8")
    os.replace(staged, path)


def main():
    path = Path(sys.argv[1])
    now = reading()
    if now is None:
        print("no finished pass with a board and a balance yet; nothing logged")
        return 0
    today = now[0].date()
    kept = LOGS / WATCH["networth_history"].format(config=path.parent.name)
    keep_from = today - datetime.timedelta(days=DAYS - 1)
    history = [row for row in recorded(kept) + [now] if row[0].date() >= keep_from]
    staged_write(kept, "".join(f"{when:{STAMP}},{alz},{stock}\n"
                               for when, alz, stock in history))
    rows = [row for row in history if row[0].date() == today]
    days = fold({day: held for day, held in saved_days(path).items()
                 if day >= keep_from}, history)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged_write(path, "\n".join(day_table(days, today) + ["", ""]
                                 + log_table(rows, today)) + "\n")
    staged_write(path.with_name(WATCH["networth_graph"]),
                 graph(rows, history, path.parent.name, today))
    print(log_table(rows, today)[-1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
