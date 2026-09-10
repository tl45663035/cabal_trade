import sys

sys.path.insert(0, r"C:\Users\Trung\Cabal")
import trade as m

m.NO_INPUT = True
failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


def direction(text):
    match = m._SORT_DIRECTION.search(text)
    return None if match is None else match.group(1).casefold()


REAL_READS = [
    ("y Price:High to v", "high"),
    ("y Price:Low to", "low"),
    ("By Price:High to Low", "high"),
    ("By Price:Low to High", "low"),
    ("By Price:Low to High", "low"),
    ("By Price:High to Low", "high"),
    ("By Price : Low to High", "low"),
    ("By Price: High to Low", "high"),
    ("y Price: to", None),
    ("Price:", None),
    ("", None),
    ("QTY Price Function", None),
]

for text, expected in REAL_READS:
    got = direction(text)
    check(got == expected,
          f"{text!r} should read as {expected}, got {got}")

for text, expected in REAL_READS:
    old_says_low = "low" in text.casefold() and "price" in text.casefold()
    new_says_low = direction(text) == "low"
    truth = expected == "low"
    check(new_says_low == truth,
          f"new reader disagrees with the truth on {text!r}")
    if old_says_low != truth:
        check(new_says_low == truth,
              f"{text!r} is a case the old reader got wrong and the new one "
              f"must get right")

old_wrong = [t for t, e in REAL_READS
             if ("low" in t.casefold() and "price" in t.casefold()) != (e == "low")]
check(len(old_wrong) >= 2,
      f"expected the corpus to contain cases the old reader failed; "
      f"found {len(old_wrong)}")


def word(text, left, top, width=60, height=15, conf=95.0):
    return m.Word(text=text, conf=conf, left=left, top=top,
                  right=left + width, bottom=top + height)


MENU = [
    word("By", 839, 219, 22), word("Price:Low", 865, 219, 84),
    word("to", 955, 219, 18), word("High", 979, 218, 40),
    word("By", 839, 255, 22), word("Price:High", 865, 254, 88),
    word("to", 957, 255, 18), word("Low", 981, 255, 36),
]

rows = {}
for line in m._text_lines(MENU):
    text = " ".join(w.text for w in line)
    match = m._SORT_DIRECTION.search(text)
    if match is None:
        continue
    lowered = text.casefold()
    if "low" not in lowered or "high" not in lowered:
        continue
    left = min(w.left for w in line)
    right = max(w.right for w in line)
    top = min(w.top for w in line)
    bottom = max(w.bottom for w in line)
    rows[match.group(1).casefold()] = ((left + right) // 2, (top + bottom) // 2)

check(set(rows) == {"low", "high"},
      f"both menu rows should be located, got {sorted(rows)}")
if "low" in rows and "high" in rows:
    lx, ly = rows["low"]
    hx, hy = rows["high"]
    check(ly < hy, "'Low to High' is the upper row")
    check(hy - ly >= 25, f"menu rows too close together: {hy - ly}px apart")
    check(219 <= ly <= 233, f"'Low to High' centre drifted to y={ly}")
    check(255 <= hy <= 269, f"'High to Low' centre drifted to y={hy}")
    for name, (x, y) in rows.items():
        left, top, right, bottom = m.PURCHASE_SORT_OPTIONS
        check(left <= x <= right and top <= y <= bottom,
              f"the {name} row centre ({x}, {y}) falls outside "
              f"PURCHASE_SORT_OPTIONS {m.PURCHASE_SORT_OPTIONS}")

TABLE_HEADER = [word("QTY", 797, 256, 40), word("Price", 865, 256, 50),
                word("Function", 1095, 256, 80)]
header_rows = 0
for line in m._text_lines(TABLE_HEADER):
    text = " ".join(w.text for w in line).casefold()
    if m._SORT_DIRECTION.search(text) and "low" in text and "high" in text:
        header_rows += 1
check(header_rows == 0,
      "the offers-table header must not be read as a sort menu row")

HALF = [word("By", 839, 219, 22), word("Price:Low", 865, 219, 84)]
half_rows = 0
for line in m._text_lines(HALF):
    text = " ".join(w.text for w in line).casefold()
    if m._SORT_DIRECTION.search(text) and "low" in text and "high" in text:
        half_rows += 1
check(half_rows == 0, "a half-read menu row must not be treated as clickable")


for name, kind in (("PURCHASE_SORT_BUTTON", "point"),
                   ("PURCHASE_SORT_OPTIONS", "box"),
                   ("PURCHASE_SORT_REGION", "box")):
    check(m._TRADE_FRAME_GEOMETRY.get(name) == kind,
          f"{name} must be registered as {kind!r} so apply_layout moves it; "
          f"an unregistered coordinate keeps its 2560x1440 value on every "
          f"other machine")

bx, by = m.PURCHASE_SORT_BUTTON
left, top, right, bottom = m.PURCHASE_SORT_REGION
check(left <= bx <= right and top <= by <= bottom,
      f"PURCHASE_SORT_BUTTON {m.PURCHASE_SORT_BUTTON} should sit inside "
      f"PURCHASE_SORT_REGION {m.PURCHASE_SORT_REGION}")

check(m.PURCHASE_SORT_OPTIONS[1] >= m.PURCHASE_SORT_REGION[3],
      f"PURCHASE_SORT_OPTIONS {m.PURCHASE_SORT_OPTIONS} overlaps "
      f"PURCHASE_SORT_REGION {m.PURCHASE_SORT_REGION}; the menu check would "
      f"read the closed control")

check(m.PURCHASE_SORT_TRIES >= 2,
      "one try is not a retry; a dropped click needs a second chance")


_real_find_words = m.find_words


def with_words(words, fn):
    m.find_words = lambda shot, region, scale=20: list(words)
    try:
        return fn()
    finally:
        m.find_words = _real_find_words


def control_saying(text):
    out, x = [], 854
    for token in text.split():
        out.append(word(token, x, 187, 9 * len(token)))
        x += 9 * len(token) + 8
    return out


for text, expected in REAL_READS:
    got = with_words(control_saying(text),
                     lambda: m.purchase_sorted_low_to_high(source=object()))
    check(got is (expected == "low"),
          f"purchase_sorted_low_to_high({text!r}) should be "
          f"{expected == 'low'}, got {got}")

check(with_words(control_saying("By Price:High to Low"),
                 lambda: m.purchase_sorted_low_to_high(source=object()))
      is False,
      "'By Price:High to Low' must NOT read as sorted low-to-high")

found = with_words(MENU, lambda: m._sort_option_rows(source=object()))
check(set(found) == {"low", "high"},
      f"_sort_option_rows should find both options, got {sorted(found)}")
check(found.get("low") == rows.get("low"),
      f"_sort_option_rows disagrees with the expected centre: "
      f"{found.get('low')} vs {rows.get('low')}")

check(with_words([], lambda: m._sort_option_rows(source=object())) == {},
      "a blank menu region must yield no clickable options")
check(with_words(TABLE_HEADER, lambda: m._sort_option_rows(source=object()))
      == {},
      "the offers-table header must yield no clickable options")

check(m.find_words is _real_find_words, "find_words was restored")


_saved = {name: getattr(m, name) for name in
          ("purchase_tab_open", "set_purchase_sort_low_to_high",
           "trade_window_open", "find_phrase", "click", "grab",
           "open_trade_window")}
try:
    calls = []
    m.purchase_tab_open = lambda source=None: True
    m.set_purchase_sort_low_to_high = (
        lambda verbose=True: calls.append("set") or True)
    out = m.open_purchase_tab(verbose=False)
    check(calls == ["set"],
          f"open_purchase_tab must set the sort when already on the tab, "
          f"calls={calls}")
    check(out is True, f"and report success, got {out!r}")

    calls.clear()
    m.set_purchase_sort_low_to_high = (
        lambda verbose=True: calls.append("set") or False)
    out = m.open_purchase_tab(verbose=False)
    check(out is False,
          f"open_purchase_tab must fail when the sort cannot be set, "
          f"got {out!r}")

    seen = {"n": 0}

    def toggling(source=None):
        seen["n"] += 1
        return seen["n"] > 1

    calls.clear()
    m.purchase_tab_open = toggling
    m.trade_window_open = lambda source=None: True
    m.find_phrase = lambda shot, phrase, region: (100, 60)
    m.click = lambda x, y, settle=0.15: None
    m.grab = lambda: object()
    m.set_purchase_sort_low_to_high = (
        lambda verbose=True: calls.append("set") or True)
    out = m.open_purchase_tab(timeout=2.0, verbose=False)
    check(calls == ["set"],
          f"open_purchase_tab must set the sort after switching tabs, "
          f"calls={calls}")
    check(out is True, f"and report success, got {out!r}")
finally:
    for name, value in _saved.items():
        setattr(m, name, value)

check(m.purchase_tab_open is _saved["purchase_tab_open"],
      "the patched names were restored")


print(f"sort_control_test: {checks} checks, {len(failures)} failure(s)")
for line in failures:
    print("  FAIL", line)
sys.exit(1 if failures else 0)
