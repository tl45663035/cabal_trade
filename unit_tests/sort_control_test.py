"""The Purchase-tab sort control: reading it, and setting it.

The reader had the grade-prefix bug in a new place -- it asked whether "low"
appeared anywhere in the control's text, and "By Price:High to Low" satisfies
that. On the 2026-08-08 run it refused the wrong sort anyway, but only because
OCR clipped the trailing "Low"; widen the crop by thirty pixels and the guard
would have passed and bought the most expensive offer on the board.

So the strings below are the ones the game actually produces, copied from live
reads rather than invented: a clipped control, a full control, and both menu
labels. Several of them are indistinguishable under the old condition.
"""
import sys

sys.path.insert(0, r"C:\Users\Trung\Cabal")
import trade as m  # noqa: E402

m.NO_INPUT = True
failures = []
checks = 0


def check(ok, what):
    global checks
    checks += 1
    if not ok:
        failures.append(what)


def direction(text):
    """The decision purchase_sorted_low_to_high makes, minus the screenshot."""
    match = m._SORT_DIRECTION.search(text)
    return None if match is None else match.group(1).casefold()


# -- what the direction regex must say about real reads --------------------
# left: text as OCR produced it. right: the sort it denotes, None = unreadable.
REAL_READS = [
    # The closed control, clipped -- this is the read from the 14:02 run.
    ("y Price:High to v", "high"),
    # The closed control, clipped, the other way -- read live on 2026-08-08.
    ("y Price:Low to", "low"),
    # The closed control read in full. THIS is the case the old condition got
    # wrong: "low" is present, and the sort is High to Low.
    ("By Price:High to Low", "high"),
    ("By Price:Low to High", "low"),
    # The menu rows, as read from the open dropdown.
    ("By Price:Low to High", "low"),
    ("By Price:High to Low", "high"),
    # Spacing OCR sometimes inserts around the colon.
    ("By Price : Low to High", "low"),
    ("By Price: High to Low", "high"),
    # Direction unread. Fails closed rather than guessing.
    ("y Price: to", None),
    ("Price:", None),
    ("", None),
    # No control in the crop at all.
    ("QTY Price Function", None),
]

for text, expected in REAL_READS:
    got = direction(text)
    check(got == expected,
          f"{text!r} should read as {expected}, got {got}")

# The specific regression: the old condition was `"low" in text and "price" in
# text`. Every string it got WRONG must now be right.
for text, expected in REAL_READS:
    old_says_low = "low" in text.casefold() and "price" in text.casefold()
    new_says_low = direction(text) == "low"
    truth = expected == "low"
    check(new_says_low == truth,
          f"new reader disagrees with the truth on {text!r}")
    if old_says_low != truth:
        # Confirms this case genuinely exercised the bug, so the test is not
        # quietly asserting something that was never broken.
        check(new_says_low == truth,
              f"{text!r} is a case the old reader got wrong and the new one "
              f"must get right")

old_wrong = [t for t, e in REAL_READS
             if ("low" in t.casefold() and "price" in t.casefold()) != (e == "low")]
check(len(old_wrong) >= 2,
      f"expected the corpus to contain cases the old reader failed; "
      f"found {len(old_wrong)}")


# -- locating the menu rows ------------------------------------------------
def word(text, left, top, width=60, height=15, conf=95.0):
    # Word carries right/bottom, not width/height. Writing it the other way
    # round is what the first draft of _sort_option_rows did, and it raised
    # AttributeError on the first real menu -- mid-click, on the Purchase tab.
    return m.Word(text=text, conf=conf, left=left, top=top,
                  right=left + width, bottom=top + height)


# Measured from the open dropdown on the reference display, plus the offers
# table showing through underneath it.
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
    # The two rows must not collapse onto each other: a 36px pitch with a 14px
    # glyph leaves plenty, and a click landing between them hits neither.
    check(hy - ly >= 25, f"menu rows too close together: {hy - ly}px apart")
    check(219 <= ly <= 233, f"'Low to High' centre drifted to y={ly}")
    check(255 <= hy <= 269, f"'High to Low' centre drifted to y={hy}")
    # Inside the crop that will be read, and inside the control's x span.
    for name, (x, y) in rows.items():
        left, top, right, bottom = m.PURCHASE_SORT_OPTIONS
        check(left <= x <= right and top <= y <= bottom,
              f"the {name} row centre ({x}, {y}) falls outside "
              f"PURCHASE_SORT_OPTIONS {m.PURCHASE_SORT_OPTIONS}")

# The offers table shows through the same band. Its header names neither
# direction, so it must not be mistaken for a menu row.
TABLE_HEADER = [word("QTY", 797, 256, 40), word("Price", 865, 256, 50),
                word("Function", 1095, 256, 80)]
header_rows = 0
for line in m._text_lines(TABLE_HEADER):
    text = " ".join(w.text for w in line).casefold()
    if m._SORT_DIRECTION.search(text) and "low" in text and "high" in text:
        header_rows += 1
check(header_rows == 0,
      "the offers-table header must not be read as a sort menu row")

# A half-read menu row must be skipped, not clicked: the offers table is
# underneath, so a click on a row we only partly saw lands on an offer.
HALF = [word("By", 839, 219, 22), word("Price:Low", 865, 219, 84)]
half_rows = 0
for line in m._text_lines(HALF):
    text = " ".join(w.text for w in line).casefold()
    if m._SORT_DIRECTION.search(text) and "low" in text and "high" in text:
        half_rows += 1
check(half_rows == 0, "a half-read menu row must not be treated as clickable")


# -- the coordinates are registered for calibration ------------------------
for name, kind in (("PURCHASE_SORT_BUTTON", "point"),
                   ("PURCHASE_SORT_OPTIONS", "box"),
                   ("PURCHASE_SORT_REGION", "box")):
    check(m._TRADE_FRAME_GEOMETRY.get(name) == kind,
          f"{name} must be registered as {kind!r} so apply_layout moves it; "
          f"an unregistered coordinate keeps its 2560x1440 value on every "
          f"other machine")

# The button must sit on the closed control, not on the menu below it.
bx, by = m.PURCHASE_SORT_BUTTON
left, top, right, bottom = m.PURCHASE_SORT_REGION
check(left <= bx <= right and top <= by <= bottom,
      f"PURCHASE_SORT_BUTTON {m.PURCHASE_SORT_BUTTON} should sit inside "
      f"PURCHASE_SORT_REGION {m.PURCHASE_SORT_REGION}")

# The two crops must not overlap, or "is the menu open" reads the closed
# control's own text and always says yes.
check(m.PURCHASE_SORT_OPTIONS[1] >= m.PURCHASE_SORT_REGION[3],
      f"PURCHASE_SORT_OPTIONS {m.PURCHASE_SORT_OPTIONS} overlaps "
      f"PURCHASE_SORT_REGION {m.PURCHASE_SORT_REGION}; the menu check would "
      f"read the closed control")

check(m.PURCHASE_SORT_TRIES >= 2,
      "one try is not a retry; a dropped click needs a second chance")


# -- the real functions, not just the regex -------------------------------
# Everything above tests _SORT_DIRECTION. That is the decision, but it is not
# the function anybody calls: purchase_sorted_low_to_high could be rewritten
# back to a substring test tomorrow and every check above would still pass.
# So the readers themselves are driven, with OCR replaced by known words.
_real_find_words = m.find_words


def with_words(words, fn):
    m.find_words = lambda shot, region, scale=20: list(words)
    try:
        return fn()
    finally:
        m.find_words = _real_find_words


def control_saying(text):
    """The closed sort control, OCR'd as `text`."""
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

# The whole point, stated once more against the function itself: the sort that
# makes row 1 the MOST expensive offer must not read as ready to buy from.
check(with_words(control_saying("By Price:High to Low"),
                 lambda: m.purchase_sorted_low_to_high(source=object()))
      is False,
      "'By Price:High to Low' must NOT read as sorted low-to-high")

# And _sort_option_rows, driven the same way.
found = with_words(MENU, lambda: m._sort_option_rows(source=object()))
check(set(found) == {"low", "high"},
      f"_sort_option_rows should find both options, got {sorted(found)}")
check(found.get("low") == rows.get("low"),
      f"_sort_option_rows disagrees with the expected centre: "
      f"{found.get('low')} vs {rows.get('low')}")

# An empty menu region means the dropdown is shut -- not that it is open with
# nothing in it. Clicking on that assumption hits the offers table.
check(with_words([], lambda: m._sort_option_rows(source=object())) == {},
      "a blank menu region must yield no clickable options")
check(with_words(TABLE_HEADER, lambda: m._sort_option_rows(source=object()))
      == {},
      "the offers-table header must yield no clickable options")

check(m.find_words is _real_find_words, "find_words was restored")


# -- open_purchase_tab must SET the sort, not merely check it -------------
# The remedy is the whole point. Checking the sort and refusing was the old
# behaviour, and it turned a dropdown left on "High to Low" into fifteen
# refused buy attempts and zero Sets bought. A test that only covers the
# reader would let that regress in silence -- it did, until a mutation of this
# exact wiring survived the suite.
_saved = {name: getattr(m, name) for name in
          ("purchase_tab_open", "set_purchase_sort_low_to_high",
           "trade_window_open", "find_phrase", "click", "grab",
           "open_trade_window")}
try:
    # Path A: already on the Purchase tab -- the early return, which is the
    # path every buy after the first one takes.
    calls = []
    m.purchase_tab_open = lambda source=None: True
    m.set_purchase_sort_low_to_high = (
        lambda verbose=True: calls.append("set") or True)
    out = m.open_purchase_tab(verbose=False)
    check(calls == ["set"],
          f"open_purchase_tab must set the sort when already on the tab, "
          f"calls={calls}")
    check(out is True, f"and report success, got {out!r}")

    # A sort that will not set makes the tab unusable, not merely untidy:
    # purchase_ready refuses on it, so saying "open" would fail a step later
    # with a less useful message.
    calls.clear()
    m.set_purchase_sort_low_to_high = (
        lambda verbose=True: calls.append("set") or False)
    out = m.open_purchase_tab(verbose=False)
    check(out is False,
          f"open_purchase_tab must fail when the sort cannot be set, "
          f"got {out!r}")

    # Path B: not on the tab yet, so it switches -- and still sets the sort.
    seen = {"n": 0}

    def toggling(source=None):
        seen["n"] += 1
        return seen["n"] > 1        # shut on the first look, open after

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
