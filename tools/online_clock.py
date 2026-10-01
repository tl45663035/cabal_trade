import datetime
import json
import os
import socket
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = json.loads((ROOT / "src_1080p" / "config.json").read_text(encoding="utf-8"))
CLOCK = SETTINGS["tools"]["online_clock"]
CACHE = ROOT / "src_1080p" / "logs" / CLOCK["cache"]
UTC = datetime.timezone.utc
NTP_EPOCH = (datetime.datetime.fromtimestamp(0, UTC)
             - datetime.datetime.fromisoformat(CLOCK["epoch"]).replace(tzinfo=UTC)).total_seconds()
REQUEST = bytes([CLOCK["request"]]) + bytes(CLOCK["packet"] - 1)


def stamp(data, at):
    whole, part = struct.unpack("!II", data[at:at + struct.calcsize("!II")])
    return whole - NTP_EPOCH + part / 2 ** CLOCK["fraction_bits"]


def ask(server):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(CLOCK["timeout"])
        sent = time.time()
        sock.sendto(REQUEST, (server, CLOCK["port"]))
        data, _ = sock.recvfrom(CLOCK["packet"])
        back = time.time()
    if len(data) < CLOCK["packet"]:
        raise OSError(f"{server} answered {len(data)} bytes")
    received, transmitted = stamp(data, CLOCK["received_at"]), stamp(data, CLOCK["transmitted_at"])
    return ((received - sent) + (transmitted - back)) / 2, back - sent


def measure():
    failed = []
    for server in CLOCK["servers"]:
        try:
            offset, trip = ask(server)
        except OSError as exc:
            failed.append(f"{server}: {exc}")
            continue
        found = {"measured": time.time(), "offset": offset, "trip": trip, "server": server}
        staged = CACHE.with_name(f"{CACHE.name}.tmp")
        staged.write_text(json.dumps(found), encoding="utf-8")
        os.replace(staged, CACHE)
        return found
    raise OSError("no time server answered: " + "; ".join(failed))


def cached():
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def offset():
    known = cached()
    if known and time.time() - known["measured"] < CLOCK["every"]:
        return known["offset"]
    try:
        return measure()["offset"]
    except OSError:
        return known["offset"] if known else None


def utc_now():
    shift = offset()
    if shift is None:
        return None
    return datetime.datetime.now(UTC) + datetime.timedelta(seconds=shift)


def main():
    found = measure()
    when = datetime.datetime.fromtimestamp(time.time() + found["offset"], UTC)
    print(f"{found['server']}: {when:%Y-%m-%d %H:%M:%S.%f} UTC; this machine's clock is "
          f"{-found['offset']:+.3f} s from it (round trip {found['trip'] * 1000:.0f} ms)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
