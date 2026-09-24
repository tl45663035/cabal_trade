import bisect
import datetime
import itertools
import json
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src_1080p"
LOGS = SRC / "logs"
DEAD = LOGS / "dead_runs"
EVENTS = LOGS / "supervise.log"
RECOVERY_FRAMES = LOGS / "supervise_frames"
RECOVERY_REELS = LOGS / "recovery_video"
FRAMES = SRC / "debug_frames"
REELS = SRC / "debug_video"
LEDGER = SRC / "sales.db"
_CONFIG = json.loads((SRC / "config.json").read_text(encoding="utf-8"))
K = _CONFIG["bugs"]
INDEX = _CONFIG["debug"]["frame_index"]
SESSION = "supervise"
SUPERVISOR = "supervisor"
RUN = "run"
BOARD = "_board"
STAMP = re.compile(r"(\d{4}-\d{2}-\d{2}_\d{6})")
LOG_NAME = re.compile(r"\d{4}-\d{2}-\d{2}_\d{6}_(\w+)\.log")
REPORT_FOLDER = re.compile(r"\d{4}-\d{2}-\d{2}_\d{6}_(?:%s|%s)" % (SESSION, RUN))
WATCHED = re.compile(r"^watching pid \d+, (\S+)\.log", re.M)
CODE = re.compile(r"^\s+code (\w+)", re.M)
SCREEN = re.compile(r"calibrated for (\d+x\d+)", re.M)
ENDED = re.compile(r"^\s+(stopped|crashed|finished): (.+)$", re.M)
SESSION_ENDED = re.compile(r"^\* (supervisor (?:cancelled|stopped|crashed)"
                           r".*),\d\d:\d\d:\d\d,\w+$", re.M)
NO_END = "no end line: still running, or killed from outside"


_NEWLINE = chr(10)


class Fail(Exception):
    pass


def machine_id():
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             r"SOFTWARE\Microsoft\Cryptography")
        return str(winreg.QueryValueEx(key, "MachineGuid")[0])
    except Exception:
        return platform.node()


def git(*args, cwd=ROOT, check=True):
    out = subprocess.run(["git", *args], cwd=str(cwd), text=True,
                         capture_output=True, encoding="utf-8",
                         errors="replace")
    if check and out.returncode != 0:
        raise Fail(f"git {' '.join(args)} failed: "
                   f"{(out.stderr or out.stdout).strip()}")
    return out


def stamp_of(name):
    found = STAMP.search(name)
    return (datetime.datetime.strptime(found.group(1), "%Y-%m-%d_%H%M%S")
            if found else None)


def read(path):
    return path.read_text(encoding="utf-8", errors="replace")


def age(path):
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def kind_of(stem):
    found = LOG_NAME.fullmatch(f"{stem}.log")
    return found.group(1) if found else None


def driver_logs():
    return sorted(p.stem for p in LOGS.glob("*.log")
                  if kind_of(p.stem) not in (None, SESSION)
                  and not kind_of(p.stem).endswith(BOARD))


def pick():
    sessions = sorted(p for p in LOGS.glob(f"*_{SESSION}.log")
                      if kind_of(p.stem) == SESSION)
    if sessions:
        session = sessions[-1]
        begun = stamp_of(session.name)
        watched = [s for s in WATCHED.findall(read(session))
                   if (LOGS / f"{s}.log").exists()]
        later = [s for s in driver_logs() if stamp_of(s) >= begun]
        return session, begun, sorted(set(watched) | set(later))
    runs = [s for s in driver_logs() if kind_of(s) == RUN]
    if not runs:
        raise Fail(f"no supervisor or run log in {LOGS}; nothing to submit")
    return None, stamp_of(runs[-1]), runs[-1:]


def copy_in(src, dest, into, kept, skipped):
    try:
        size = src.stat().st_size
        if size > K["max_file_mb"] * 1024 * 1024:
            skipped.append({"file": src.name, "mb": round(size / 1048576, 1)})
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dest))
    except FileNotFoundError:
        return False
    kept.append({"file": dest.relative_to(into).as_posix(),
                 "mb": round(size / 1048576, 2)})
    return True


def board_rows():
    if not LEDGER.exists():
        return []
    con = sqlite3.connect(f"file:{LEDGER.as_posix()}?mode=ro", uri=True)
    try:
        return [dict(zip(("row", "at", "item", "qty", "price", "buy_cost",
                          "floor_at"), r))
                for r in con.execute("SELECT row, at, item, qty, price, "
                                     "buy_cost, floor_at FROM board "
                                     "ORDER BY row")]
    finally:
        con.close()


def ended(text):
    found = ENDED.findall(text)
    return f"{found[-1][0]}: {found[-1][1]}" if found else None


def session_ended(text):
    found = SESSION_ENDED.findall(text)
    return found[-1] if found else NO_END


def frames_of(stem, newest):
    files, metas = {}, {}
    for home in (FRAMES / stem, DEAD / stem):
        if not home.is_dir():
            continue
        for f in home.glob("*.png"):
            files.setdefault(f.name, f)
        index = home / INDEX
        if not index.exists():
            continue
        for line in read(index).splitlines():
            try:
                meta = json.loads(line)
            except ValueError:
                continue
            if isinstance(meta, dict) and "n" in meta and "file" in meta:
                metas.setdefault(meta["file"], meta)
    if not files and newest:
        begun = stamp_of(stem).timestamp()
        files = {f.name: f for f in FRAMES.glob("*.png")
                 if (when := age(f)) is not None and when >= begun}
    indexed = sorted(((metas[n], p) for n, p in files.items() if n in metas),
                     key=lambda mp: mp[0]["n"])
    loose = [p for n, p in sorted(files.items()) if n not in metas]
    last = 0
    for meta, _path in indexed:
        if meta.get("log") is None:
            meta["log"] = last
        last = meta["log"]
    return indexed, loose, sorted(metas.values(), key=lambda m: m["n"])


def lines_matching(raw, needle):
    out, start, want = [], 0, needle.encode("utf-8")
    while True:
        at = raw.find(want, start)
        if at < 0:
            return out
        begin = raw.rfind(b"\n", 0, at) + 1
        end = raw.find(b"\n", at)
        end = len(raw) if end < 0 else end
        out.append((begin, raw[begin:end].decode("utf-8",
                                               "replace").strip()))
        start = end + 1


def spread(hits):
    cap = K["events_per_pattern"]
    if len(hits) <= cap:
        return hits
    return hits[:cap // 2] + hits[len(hits) - (cap - cap // 2):]


def tiers(stem, raw, text, indexed, loose):
    ordered = indexed + [(None, p) for p in loose]
    offsets = [m["log"] for m, _p in indexed]
    why = ended(text) or NO_END
    blocks, events, counts = [], [], {}
    for needle in K["event_patterns"]:
        hits = lines_matching(raw, needle)
        if hits:
            counts[needle] = len(hits)
        for offset, line in spread(hits):
            i = bisect.bisect_right(offsets, offset)
            events.append({"pattern": needle, "line": line, "log": offset,
                           "near": (indexed[min(i, len(indexed) - 1)][0]["at"]
                                    if indexed else None)})
            before = indexed[max(0, i - K["frames_before"]):i]
            after = indexed[i:i + K["frames_after"]]
            blocks.append([(stem, m, p, f"{needle!r}: {line}")
                           for pair in itertools.zip_longest(
                               after, reversed(before))
                           for m, p in (x for x in pair if x is not None)])
    return {"why": why, "counts": counts, "events": events,
            "end": [(stem, m, p, f"where it ended: {why}")
                    for m, p in ordered[-K["frames_before"]:]],
            "blocks": blocks,
            "tail": [(stem, m, p, "going back from where it ended")
                     for m, p in reversed(ordered)]}


def choose(logs):
    picked, reasons = [], {}
    newest_first = list(reversed(list(logs.values())))

    def take(entries, note=True):
        for stem, meta, path, why in entries:
            if path in reasons:
                if note and why not in reasons[path]:
                    reasons[path].append(why)
                continue
            if len(picked) < K["max_frames"]:
                picked.append((stem, meta, path))
                reasons[path] = [why]

    take(e for t in newest_first for e in t["end"])
    for turn in itertools.zip_longest(*(t["blocks"] for t in newest_first),
                                      fillvalue=[]):
        for block in turn:
            take(block)
    take((e for turn in itertools.zip_longest(*(t["tail"]
                                                 for t in newest_first))
          for e in turn if e is not None), note=False)
    return picked, reasons


def table(rows):
    if not rows:
        return []
    widths = [max(len(r[c]) for r in rows) for c in range(len(rows[0]))]
    return ["  " + "  ".join(v.ljust(w) for v, w in zip(r, widths)).rstrip()
            for r in rows]


def gather(session, begun, stems, into):
    into.mkdir(parents=True, exist_ok=True)
    kept, skipped = [], []
    everything = driver_logs()
    newest = everything[-1] if everything else None
    logs = {}
    for stem in stems:
        path = LOGS / f"{stem}.log"
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="replace").replace("\r\n", _NEWLINE)
        indexed, loose, index = frames_of(stem, stem == newest)
        logs[stem] = dict(tiers(stem, raw, text, indexed, loose), path=path,
                          text=text, index=index,
                          kept=len(indexed) + len(loose))
    picked, reasons = choose(logs)
    summaries, listing = [], []
    for stem, t in logs.items():
        home = into / stem
        copy_in(t["path"], home / t["path"].name, into, kept, skipped)
        board = LOGS / f"{stem}{BOARD}.log"
        if board.exists():
            copy_in(board, home / board.name, into, kept, skipped)
        mine = sorted(((m, p) for s, m, p in picked if s == stem),
                      key=lambda mp: (mp[0] is None,
                                      mp[0]["n"] if mp[0] else 0, mp[1].name))
        sent = [(m, p) for m, p in mine
                if copy_in(p, home / "frames" / p.name, into, kept, skipped)]
        reels = sorted((DEAD / stem).glob("*.mp4")) + (
            sorted(REELS.glob("*.mp4")) if stem == newest else [])
        for f in reels:
            copy_in(f, home / "frames" / f.name, into, kept, skipped)
        if t["index"]:
            (home / "frames").mkdir(parents=True, exist_ok=True)
            (home / "frames" / INDEX).write_text(
                "".join(json.dumps(m) + _NEWLINE for m in t["index"]),
                encoding="utf-8")
        listing.append(f"{stem}: {len(sent)} of {t['kept']} frames sent; "
                       f"{t['why']}")
        listing.extend(table([(
            (m or {}).get("at", ""), str((m or {}).get("n", "")), p.name,
            (m or {}).get("phase", ""), (m or {}).get("step", ""),
            " | ".join(reasons[p])) for m, p in sent]))
        listing.append("")
        code = CODE.search(t["text"])
        screen = SCREEN.search(t["text"])
        summaries.append({
            "log": stem,
            "kind": kind_of(stem),
            "code": code.group(1) if code else None,
            "screen": screen.group(1) if screen else None,
            "reason": t["why"],
            "frames": {"kept": t["kept"], "indexed": len(t["index"]),
                       "sent": len(sent)},
            "flagged": t["counts"],
            "events": t["events"],
        })
    (into / "frames.txt").write_text(_NEWLINE.join(listing), encoding="utf-8")
    if session is not None:
        copy_in(session, into / SUPERVISOR / session.name, into, kept,
                skipped)
    if EVENTS.exists():
        copy_in(EVENTS, into / SUPERVISOR / EVENTS.name, into, kept, skipped)
    for folder in (RECOVERY_FRAMES, RECOVERY_REELS):
        if not folder.is_dir():
            continue
        for f in sorted(folder.iterdir()):
            when = stamp_of(f.name)
            if when is not None and when >= begun:
                copy_in(f, into / SUPERVISOR / folder.name / f.name, into,
                        kept, skipped)
    for name in ("config.json", "calibration.json"):
        copy_in(SRC / name, into / name, into, kept, skipped)
    (into / "board.json").write_text(
        json.dumps(board_rows(), indent=2), encoding="utf-8")
    manifest = {
        "supervisor": session.name if session is not None else None,
        "supervisor_ended": (session_ended(read(session))
                             if session is not None else None),
        "since": begun.strftime("%Y-%m-%dT%H:%M:%S"),
        "machine": platform.node(),
        "machine_id": machine_id(),
        "submitted_at": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "max_frames": K["max_frames"],
        "logs": summaries,
        "files": kept,
        "skipped_over_max_file_mb": skipped,
    }
    (into / "manifest.json").write_text(json.dumps(manifest, indent=2),
                                        encoding="utf-8")
    return manifest


def not_allowed(said):
    lowered = (said or "").lower()
    return any(mark in lowered for mark in
               ("error: 403", "error: 401", "authentication failed",
                "could not read username", "permission denied", "denied to",
                "write access", "repository not found",
                "does not appear to be a git"))


def resolve_rebase(work, mine):
    while True:
        conflicted = git("diff", "--name-only", "--diff-filter=U", cwd=work,
                         check=False).stdout.split()
        if not conflicted:
            return
        for path in conflicted:
            side = "--theirs" if path.startswith(mine) else "--ours"
            if git("checkout", side, "--", path, cwd=work,
                   check=False).returncode != 0:
                git("rm", "-q", "--cached", "--", path, cwd=work, check=False)
                Path(work, path).unlink(missing_ok=True)
            else:
                git("add", "-f", "--", path, cwd=work)
        out = git("-c", "core.editor=true", "rebase", "--continue", cwd=work,
                  check=False)
        if out.returncode == 0:
            return


def publish(name, manifest_of):
    remote, branch = K["remote"], K["branch"]
    upstream = f"{remote}/{branch}"
    mine = f"{K['folder']}/{machine_id()}"
    git("fetch", "-q", remote, branch)
    work = Path(tempfile.mkdtemp(prefix="submit_bug_"))
    git("worktree", "add", "-q", "--detach", str(work), upstream)
    try:
        base = work / mine
        folder = base / name
        if base.exists():
            for old in base.iterdir():
                if old == folder or not REPORT_FOLDER.fullmatch(old.name):
                    if old.is_dir():
                        shutil.rmtree(old)
                    else:
                        old.unlink()
        manifest = manifest_of(folder)
        reports = sorted(p for p in base.iterdir()
                         if p.is_dir() and REPORT_FOLDER.fullmatch(p.name))
        for old in reports[:-K["keep_reports"]]:
            shutil.rmtree(old)
        git("add", "-A", "-f", "--", mine, cwd=work)
        final = (manifest["logs"][-1]["reason"] if manifest["logs"]
                 else manifest["supervisor_ended"])
        message = (f"bug: {name} on {manifest['machine']}: "
                   f"{len(manifest['logs'])} log(s); last: {final}")
        git("commit", "-q", "-m", message, cwd=work)
        for attempt in range(1, K["push_retries"] + 1):
            pushed = git("push", "-q", remote, f"HEAD:{branch}", cwd=work,
                         check=False)
            if pushed.returncode == 0:
                return git("rev-parse", "--short", "HEAD", cwd=work).stdout.strip(), manifest
            said = pushed.stderr.strip().splitlines()
            last = said[-1] if said else ""
            if not_allowed(pushed.stderr):
                raise Fail(_NEWLINE.join([
                    last,
                    f"  this machine's git credential cannot write to "
                    f"{remote}; nothing was sent and nothing was changed "
                    f"here.",
                    f"  give it one that can, then run this again:",
                    f"    gh auth login    (then: gh auth setup-git)",
                    f"  or drop the stored one so git asks on the next push:",
                    f"    cmdkey /delete:git:https://github.com",
                ]))
            print(f"  push attempt {attempt} rejected: {last}")
            git("fetch", "-q", remote, branch)
            rebased = git("rebase", upstream, cwd=work, check=False)
            if rebased.returncode != 0:
                resolve_rebase(work, mine)
        raise Fail(f"the push was rejected {K['push_retries']} time(s)")
    finally:
        git("worktree", "remove", "--force", str(work), check=False)
        shutil.rmtree(work, ignore_errors=True)


def main():
    session, begun, stems = pick()
    name = session.stem if session is not None else stems[-1]
    print(f"submitting {name} from {platform.node()} ({machine_id()}): "
          f"every log since {begun:%Y-%m-%d %H:%M:%S}")
    commit, manifest = publish(
        name, lambda into: gather(session, begun, stems, into))
    for entry in manifest["logs"]:
        frames = entry["frames"]
        print(f"  {entry['log']}  code {entry['code']}  {entry['reason']}")
        print(f"      frames: {frames['sent']} sent of {frames['kept']} kept, "
              f"{frames['indexed']} indexed")
        if entry["flagged"]:
            print("      flagged: " + ", ".join(
                f"{pattern!r} x{count}"
                for pattern, count in entry["flagged"].items()))
    total = sum(f["mb"] for f in manifest["files"])
    print(f"  {len(manifest['files'])} file(s), {total:.1f} MB"
          + (f"; supervisor: {manifest['supervisor_ended']}"
             if session is not None else "; no supervisor log"))
    for f in manifest["skipped_over_max_file_mb"]:
        print(f"  skipped {f['file']} ({f['mb']} MB, over "
              f"{K['max_file_mb']} MB)")
    print(f"  pushed {commit} to {K['remote']}/{K['branch']} as "
          f"{K['folder']}/{machine_id()}/{name}; the last "
          f"{K['keep_reports']} reports from this machine stay alongside")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Fail as exc:
        print(f"  {exc}")
        sys.exit(1)
