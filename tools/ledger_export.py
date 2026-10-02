import csv
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = json.loads((ROOT / "src_1080p" / "config.json").read_text(encoding="utf-8"))
SUP = SETTINGS["supervise"]
LEDGER = ROOT / "src_1080p" / "sales.db"


def snapshot():
    live = sqlite3.connect(f"file:{LEDGER.as_posix()}?mode=ro", uri=True)
    copy = sqlite3.connect(":memory:")
    try:
        live.backup(copy)
    finally:
        live.close()
    return copy


def write(copy, table, folder):
    cursor = copy.execute(f"SELECT * FROM {table} ORDER BY 1")
    path = folder / f"{table}.csv"
    staged = path.with_name(f"{path.name}.tmp")
    with open(staged, "w", encoding="utf-8", newline="") as out:
        rows = csv.writer(out, lineterminator="\n")
        rows.writerow([column[0] for column in cursor.description])
        rows.writerows(cursor)
    os.replace(staged, path)
    return path


def main():
    config = sys.argv[1]
    folder = ROOT / SUP["report_dir"] / config / SUP["artifacts_dir"]
    folder.mkdir(parents=True, exist_ok=True)
    copy = snapshot()
    for table in SUP["ledger_tables"]:
        print(write(copy, table, folder).relative_to(ROOT).as_posix())
    return 0


if __name__ == "__main__":
    sys.exit(main())
