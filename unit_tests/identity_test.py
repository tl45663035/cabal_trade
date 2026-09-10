
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import trade

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


def as_rows(raw: list[dict]) -> list[trade.Row]:
    return [trade.Row(index=r["index"], name=r["name"],
                      change=tuple(r["change"]), top=r["top"],
                      bottom=r["bottom"], action=r["action"],
                      price=r["price"], qty=r["qty"]) for r in raw]


def twin(a: trade.Row, b: trade.Row) -> bool:
    return (a.name, a.price, a.qty, a.action) == (b.name, b.price, b.qty,
                                                  b.action)


def report(title: str, bad: list[str]) -> bool:
    detail = ""
    if bad:
        detail = (f"{len(bad)} violation(s); first 5:\n           "
                  + "\n           ".join(bad[:5]))
    return check(title, not bad, detail)


def main() -> int:
    if not BASELINE.exists():
        print("no baseline_rows.json yet - nothing to replay.")
        return 0

    data = json.loads(BASELINE.read_text())
    tables = [as_rows(v) for v in data.values() if v]
    print(f"replaying {len(tables):,} recorded tables "
          f"({sum(len(t) for t in tables):,} rows)\n")
    if not tables:
        return 0

    wrong_row, strict_wrong, strict_silent = [], [], []
    collect_wrong, dropped_wrong, partial_wrong = [], [], []
    tables_with_twins = 0
    twin_rows = 0
    sold_seen = 0
    family_sizes = Counter()

    for table, name in zip(tables, sorted(data)):
        live = [r for r in table if r.action in ("change", "receive")]
        has_twin = any(twin(a, b) for i, a in enumerate(live)
                       for b in live[i + 1:])
        tables_with_twins += has_twin

        for row in live:
            siblings = [r for r in live if twin(r, row)]
            twin_rows += len(siblings) > 1
            family_sizes[len(siblings)] += 1

            ref = trade.RowRef.of(row, table)
            found, note = trade.locate_row(live, ref)
            if found is None:
                wrong_row.append(f"{name} row {row.index} ({row.name!r}): "
                                 f"lost entirely, note={note!r}")
            elif not twin(found, row):
                wrong_row.append(
                    f"{name} row {row.index}: {row.name!r} x{row.qty} "
                    f"resolved to row {found.index} {found.name!r} x{found.qty}")

            s_found, s_note = trade.locate_row(live, ref, strict=True)
            if s_found is not None and not twin(s_found, row):
                strict_wrong.append(
                    f"{name} row {row.index}: strict resolved to a different "
                    f"listing (row {s_found.index})")
            if len(siblings) > 1 and s_found is not None:
                strict_silent.append(
                    f"{name} row {row.index}: {len(siblings)} identical rows "
                    f"but strict returned one anyway")

            fam = trade.listing_family(live, row.name, row.price)
            before = trade.family_quantities(fam)

            if row.action == "receive":
                sold_seen += 1
                after = trade.family_quantities([r for r in fam
                                                 if r is not row])
                lost, gained = trade.collect_delta(before, after)
                if not (len(lost) == 1 and not gained):
                    collect_wrong.append(
                        f"{name} row {row.index}: collecting {row.name!r} "
                        f"x{row.qty} gave lost={lost} gained={gained} "
                        f"(family of {len(fam)})")

                if isinstance(row.qty, int) and row.qty > 1:
                    smaller = row.qty - 1
                    after_p = trade.family_quantities(
                        [r for r in fam if r is not row]) + [smaller]
                    after_p = sorted(after_p, key=lambda q: (q is None, q))
                    lost_p, gained_p = trade.collect_delta(before, after_p)
                    if not (len(lost_p) == 1 and gained_p == [smaller]):
                        partial_wrong.append(
                            f"{name} row {row.index}: partial {row.qty}->"
                            f"{smaller} gave lost={lost_p} gained={gained_p}")

            lost_d, gained_d = trade.collect_delta(before, list(before))
            if lost_d or gained_d:
                dropped_wrong.append(
                    f"{name} row {row.index}: an unchanged table read as "
                    f"lost={lost_d} gained={gained_d}")

    print("--- finding a row again from its own reference ---")
    report("every live row resolves to itself or an exact twin", wrong_row)

    print("\n--- strict mode must refuse rather than guess ---")
    report("strict never returns a different listing", strict_wrong)
    report("strict refuses whenever an identical twin exists", strict_silent)

    print("\n--- the collect decision, on real tables ---")
    report("collecting a sale reads as exactly one stack lost", collect_wrong)
    report("a partial sale names the remainder", partial_wrong)
    report("an unchanged table reads as nothing moved", dropped_wrong)

    print("\n--- observed, for context ---")
    print(f"  tables carrying at least one identical pair: "
          f"{tables_with_twins:,} / {len(tables):,} "
          f"({tables_with_twins / len(tables):.1%})")
    print(f"  live rows that have an identical twin      : {twin_rows:,}")
    print(f"  sold rows replayed through the collect check: {sold_seen:,}")
    print("  family sizes: "
          + ", ".join(f"{k} row(s) x{v:,}"
                      for k, v in sorted(family_sizes.items())))

    print(f"\n{'-' * 70}")
    print(f"{checks} propert(ies) over {len(tables):,} tables, "
          f"{len(failures)} FAILED")
    for line in failures:
        print(f"  FAIL {line}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
