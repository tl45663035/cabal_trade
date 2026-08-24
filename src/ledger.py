import datetime
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().with_name("sales.db")

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS purchases (
           id    INTEGER PRIMARY KEY AUTOINCREMENT,
           at    TEXT    NOT NULL,
           run   TEXT,
           item  TEXT    NOT NULL,
           price INTEGER,
           spend INTEGER,
           qty   INTEGER,
           note  TEXT
       )""",
    """CREATE TABLE IF NOT EXISTS sales (
           id       INTEGER PRIMARY KEY AUTOINCREMENT,
           at       TEXT    NOT NULL,
           run      TEXT,
           item     TEXT    NOT NULL,
           price    INTEGER,
           proceeds INTEGER,
           qty      INTEGER,
           note     TEXT
       )""",
)

_RUN = None


def start(stamp=None):
    global _RUN
    _RUN = stamp or datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with sqlite3.connect(DB) as db:
        for statement in _SCHEMA:
            db.execute(statement)
    return _RUN


def _write(table, columns, values):
    if _RUN is None:
        start()
    at = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    names = ", ".join(("at", "run") + columns)
    holes = ", ".join("?" * (len(columns) + 2))
    with sqlite3.connect(DB) as db:
        db.execute(f"INSERT INTO {table} ({names}) VALUES ({holes})",
                   (at, _RUN) + values)


def bought(item, price, spend, qty, note=None):
    _write("purchases", ("item", "price", "spend", "qty", "note"),
           (item, int(price or 0), int(spend or 0), int(qty or 0), note))


def sold(item, price, proceeds, qty, note=None):
    _write("sales", ("item", "price", "proceeds", "qty", "note"),
           (item, int(price or 0), int(proceeds or 0), int(qty or 0), note))
