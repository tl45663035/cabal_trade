"""Every configured floor, checked generically against the whole catalogue.

    py unit_tests\\floor_catalogue_test.py

Driven from ITEM_PRICE_FLOORS rather than from a hand-written list, so adding a
floor adds its coverage automatically instead of quietly having none. Three
questions per entry:

  1. does its own item get the floor, including through OCR damage?
  2. does it capture any OTHER floored item? (a floor is a MINIMUM, so a
     mis-tagged cheap item is listed at the expensive item's price, never
     sells, and pays a percentage fee on the inflated figure)
  3. does it capture any item the reader has actually produced that should
     have no floor at all?

Question 3 is checked against every distinct name in baseline_rows.json -- real
reader output, not invented strings -- so a new token is measured against what
this account really lists.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import trade as m  # noqa: E402

BASELINE = HERE / "baseline_rows.json"

failures: list[str] = []
checks = 0


def check(name: str, ok: bool, detail: str = "") -> bool:
    global checks
    checks += 1
    print(("[  ok  ] " if ok else "[ FAIL ] ") + name
          + ("" if ok or not detail else f"\n           {detail}"))
    if not ok:
        failures.append(f"{name}: {detail}")
    return ok


def damaged(name: str) -> list[tuple[str, str]]:
    """Plausible OCR corruptions of a catalogue name."""
    out = [("dropped space", name.replace(" ", "", 1)),
           ("o -> 0", name.replace("o", "0", 1)),
           ("O -> 0", name.replace("O", "0", 1)),
           ("leading glyph clipped", name[1:]),
           ("dropped letter", name[:5] + name[6:])]
    # A trailer the column was too narrow to hold.
    if " " in name:
        out.append(("trailer clipped", name.rsplit(" ", 1)[0]))
    return [(why, bad) for why, bad in out if bad and bad != name]


def main() -> int:
    floors = list(m.ITEM_PRICE_FLOORS)
    print(f"{len(floors)} configured floor(s)\n")
    for token, label, floor in floors:
        print(f"  {token:10} {label:44} {floor:>14,}")
    print()

    print("--- 1. each floor claims its own item ---")
    for token, label, floor in floors:
        got = m.item_price_floor(label)
        check(f"{label!r} -> {floor:,}", got == floor, f"got {got:,}")

    print("\n--- 1b. ...and keeps it through OCR damage ---")
    # NEVER BELOW the entry's floor, rather than exactly equal to it.
    #
    # Equality was right while every catalogue name was distinct. It became
    # wrong when a prefix-related PAIR was catalogued: "Epic Booster (High)"
    # and "(Highest)" cannot be told apart once damaged, so the lookup returns
    # the HIGHER of the two, and asserting equality here demanded the cheaper
    # floor for a read that might be either item -- exactly the underpricing
    # this whole area exists to prevent.
    #
    # The (High) entry was removed on 2026-08-07, so no such pair is catalogued
    # at the moment. "Never below" stays anyway: it is the property that
    # actually matters, it costs nothing while names are distinct, and the next
    # near-name added to the catalogue would silently reintroduce the problem
    # if this had been tightened back to equality in the meantime.
    for token, label, floor in floors:
        lost = [(why, bad) for why, bad in damaged(label)
                if m.item_price_floor(bad) < floor]
        check(f"{token}: survives every damaged read", not lost,
              "; ".join(f"{why}: {bad!r} -> "
                        f"{m.item_price_floor(bad):,}" for why, bad in lost))

    print("\n--- 2. no floor claims another floored item ---")
    for token, label, floor in floors:
        for other_token, other_label, other_floor in floors:
            if other_label == label:
                continue
            got = m.item_price_floor(other_label)
            check(f"{other_label[:28]!r} does not take {token}'s floor",
                  got != floor or other_floor == floor,
                  f"got {got:,}, {token}'s floor is {floor:,} -- one item's "
                  f"floor landing on another prices it wrongly in whichever "
                  f"direction the numbers happen to fall")

    print("\n--- 3. nothing else this account lists picks up a floor ---")
    if not BASELINE.exists():
        print("  no baseline yet - skipped")
    else:
        names = set()
        for rows in json.loads(BASELINE.read_text()).values():
            for r in rows:
                n = (r.get("name") or "").strip()
                if n and n != "(empty)":
                    names.add(n)
        expected = {}
        for n in sorted(names):
            got = m.item_price_floor(n)
            if got:
                expected[n] = got
        print(f"  {len(names)} distinct recorded names; "
              f"{len(expected)} carry a floor")
        for n, got in sorted(expected.items()):
            print(f"     {got:>14,}  {n!r}")
        # Every floored name must correspond to a configured entry, by
        # similarity to its catalogue name -- not merely "some floor applied".
        stray = []
        for n, got in expected.items():
            owner = next((lab for _t, lab, f in floors if f == got), None)
            if owner is None:
                stray.append(f"{n!r} -> {got:,} which no entry configures")
                continue
            from difflib import SequenceMatcher
            ratio = SequenceMatcher(None, m._floor_key(owner),
                                    m._floor_key(n)).ratio()
            if ratio < 0.55:
                stray.append(f"{n!r} took {owner!r}'s floor at similarity "
                             f"{ratio:.2f}")
        check("every floored name resembles the entry that floored it",
              not stray, "; ".join(stray))

    print("\n--- 4. the known over-match, asserted so it cannot drift ---")
    # A different pack size scores >0.94 against the x400 name, so the
    # similarity route claims it whatever token is chosen. Asserted as CURRENT
    # behaviour: if someone narrows it later this test fails and says why,
    # rather than the change going unnoticed.
    gem = next((f for t, lab, f in m.ITEM_PRICE_FLOORS
                if "gempack" in t), None)
    if gem:
        for other in ("Force Gem Package (x100)", "Force Gem Package (x40)"):
            got = m.item_price_floor(other)
            check(f"{other!r} still inherits the x400 floor (known)",
                  got == gem,
                  f"got {got:,} -- if this now differs, the over-match has "
                  f"been fixed and this check should become the opposite "
                  f"assertion")
        check("'Force Gem' alone does NOT inherit it",
              m.item_price_floor("Force Gem") != gem,
              f"got {m.item_price_floor('Force Gem'):,} -- that is why the "
              f"token is 'gempack' and not 'gem'")

    print(f"\n{'-' * 70}")
    print(f"{checks} checks over {len(floors)} floor(s), {len(failures)} FAILED")
    for line in failures:
        print(f"  FAIL {line}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
