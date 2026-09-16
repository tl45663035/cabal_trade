import datetime
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
K = json.loads((SRC / "config.json").read_text(encoding="utf-8"))["bugs"]
STAMP = re.compile(r"(\d{4}-\d{2}-\d{2}_\d{6})")
CODE = re.compile(r"^\s+code (\w+)", re.M)
SCREEN = re.compile(r"calibrated for (\d+x\d+)", re.M)
STOPPED = re.compile(r"^\s+stopped: (.+)$", re.M)


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


def run_stems():
    return sorted(p.name[:-len("_run.log")] for p in LOGS.glob("*_run.log"))


def pick_run(argv):
    if len(argv) > 1:
        stem = argv[1].removesuffix("_run.log").removesuffix("_run")
        if not (LOGS / f"{stem}_run.log").exists():
            raise Fail(f"no run log for {stem!r} in {LOGS}")
        return stem
    dead = sorted(p for p in DEAD.glob("*_run") if p.is_dir())
    if dead:
        return dead[-1].name.removesuffix("_run")
    stems = run_stems()
    if not stems:
        raise Fail(f"no run logs in {LOGS}; nothing to submit")
    return stems[-1]


def window(stem):
    start = stamp_of(stem)
    later = [s for s in run_stems() if s > stem]
    end = (stamp_of(later[0]) if later else datetime.datetime.now())
    return start, end + datetime.timedelta(minutes=K["frames_after_minutes"])


def copy_in(src, dest, into, kept, skipped):
    size = src.stat().st_size
    if size > K["max_file_mb"] * 1024 * 1024:
        skipped.append({"file": src.name, "mb": round(size / 1048576, 1)})
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(src), str(dest))
    kept.append({"file": dest.relative_to(into).as_posix(),
                 "mb": round(size / 1048576, 2)})


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


def gather(stem, into):
    kept, skipped = [], []
    start, end = window(stem)
    log = LOGS / f"{stem}_run.log"
    text = log.read_text(encoding="utf-8", errors="replace")
    copy_in(log, into / "run.log", into, kept, skipped)
    board = LOGS / f"{stem}_run_board.log"
    if board.exists():
        copy_in(board, into / "board.log", into, kept, skipped)
    dead = DEAD / f"{stem}_run"
    if dead.is_dir():
        for f in sorted(dead.iterdir()):
            copy_in(f, into / "frames" / f.name, into, kept, skipped)
    if run_stems() and run_stems()[-1] == stem:
        live = sorted(FRAMES.glob("*.png"))[-K["live_frames"]:]
        for f in live:
            copy_in(f, into / "frames" / f.name, into, kept, skipped)
        for f in sorted(REELS.glob("*.mp4")):
            copy_in(f, into / "frames" / f.name, into, kept, skipped)
    for f in sorted(LOGS.glob("*_supervise.log")):
        if stem in f.read_text(encoding="utf-8", errors="replace"):
            copy_in(f, into / "supervisor" / f.name, into, kept, skipped)
    if EVENTS.exists():
        copy_in(EVENTS, into / "supervisor" / EVENTS.name, into, kept, skipped)
    for folder in (RECOVERY_FRAMES, RECOVERY_REELS):
        if not folder.is_dir():
            continue
        for f in sorted(folder.iterdir()):
            when = stamp_of(f.name)
            if when is not None and start <= when <= end:
                copy_in(f, into / "supervisor" / folder.name / f.name, into,
                        kept, skipped)
    for name in ("config.json", "calibration.json"):
        copy_in(SRC / name, into / name, into, kept, skipped)
    (into / "board.json").write_text(
        json.dumps(board_rows(), indent=2), encoding="utf-8")
    code = CODE.search(text)
    screen = SCREEN.search(text)
    stopped = STOPPED.findall(text)
    manifest = {
        "run": stem,
        "machine": platform.node(),
        "machine_id": machine_id(),
        "submitted_at": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "code": code.group(1) if code else None,
        "screen": screen.group(1) if screen else None,
        "reason": stopped[-1] if stopped else "ended without a reason line",
        "window": [start.strftime("%Y-%m-%dT%H:%M:%S"),
                   end.strftime("%Y-%m-%dT%H:%M:%S")],
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


def publish(stem, manifest_of):
    remote, branch = K["remote"], K["branch"]
    upstream = f"{remote}/{branch}"
    mine = f"{K['folder']}/{machine_id()}"
    git("fetch", "-q", remote, branch)
    work = Path(tempfile.mkdtemp(prefix="submit_bug_"))
    git("worktree", "add", "-q", "--detach", str(work), upstream)
    try:
        folder = work / mine
        if folder.exists():
            shutil.rmtree(folder)
        manifest = manifest_of(folder)
        git("add", "-A", "-f", "--", mine, cwd=work)
        message = (f"bug: {stem} on {manifest['machine']}: "
                   f"{manifest['reason']}")
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


def main(argv):
    stem = pick_run(argv)
    print(f"submitting {stem} from {platform.node()} ({machine_id()})")
    commit, manifest = publish(stem, lambda into: gather(stem, into))
    total = sum(f["mb"] for f in manifest["files"])
    print(f"  {len(manifest['files'])} file(s), {total:.1f} MB, code "
          f"{manifest['code']}, screen {manifest['screen']}")
    print(f"  reason: {manifest['reason']}")
    for f in manifest["skipped_over_max_file_mb"]:
        print(f"  skipped {f['file']} ({f['mb']} MB, over "
              f"{K['max_file_mb']} MB)")
    print(f"  pushed {commit} to {K['remote']}/{K['branch']} as "
          f"{K['folder']}/{machine_id()}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Fail as exc:
        print(f"  {exc}")
        sys.exit(1)
