"""Recording keeps a rolling window instead of stopping dead.

RECORD_LIMIT used to STOP recording at 12,000 frames, announcing it once on
stderr. A 494-cycle run on 2026-08-05 therefore produced no diagnostic frames
at all -- and reconstructing a failure from the frames it left behind is how
every bug found on 2026-08-04/05 was found. The newest frames are also the
useful ones: they match the current build and the current shop.

The dangerous part is not the deleting, it is keeping the index in step. A
frame with no index line is an orphan no test can interpret; an index line with
no frame makes the corpus suite assert against a file that is not there. Both
directions are asserted here.

Uses a temporary RECORD_DIR throughout -- nothing here may touch the real
corpus, which is 12,000 frames of live evidence.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from harness import check, section, summary  # noqa: E402

import trade  # noqa: E402


class Corpus:
    """A throwaway RECORD_DIR seeded with `n` frames and a matching index."""

    def __init__(self, n, keep=None, slack=None):
        self.n, self.keep, self.slack = n, keep, slack

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="prune_test_"))
        self.saved = (trade.RECORD_DIR, trade.RECORD_KEEP,
                      trade.RECORD_PRUNE_SLACK, trade._record_seq)
        trade.RECORD_DIR = self.dir
        if self.keep is not None:
            trade.RECORD_KEEP = self.keep
        if self.slack is not None:
            trade.RECORD_PRUNE_SLACK = self.slack
        index = self.dir / "run_index.jsonl"
        with index.open("w", encoding="utf-8") as fh:
            for i in range(1, self.n + 1):
                name = f"run_{i:05d}.png"
                (self.dir / name).write_bytes(b"x")
                fh.write(json.dumps({"file": name, "label": "t",
                                     "at": "2026-08-05T00:00:00"}) + "\n")
        return self

    def __exit__(self, *exc):
        (trade.RECORD_DIR, trade.RECORD_KEEP,
         trade.RECORD_PRUNE_SLACK, trade._record_seq) = self.saved
        shutil.rmtree(self.dir, ignore_errors=True)
        return False

    def frames(self):
        return sorted(p.name for p in self.dir.glob("run_*.png"))

    def indexed(self):
        path = self.dir / "run_index.jsonl"
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                # A frameless index row carries "file": null -- the profiling
                # labels write a measurement with no screenshot. This function
                # returns FRAME NAMES, so those rows are not entries in it.
                name = json.loads(line).get("file")
                if name:
                    out.append(name)
        return out


# ===========================================================================
section("below the threshold, nothing is touched")

with Corpus(50, keep=1000, slack=100) as c:
    removed = trade.prune_recordings()
    check("under the limit: removes nothing", removed == 0, f"{removed}")
    check("under the limit: frames intact", len(c.frames()) == 50,
          f"{len(c.frames())}")

with Corpus(1050, keep=1000, slack=100) as c:
    removed = trade.prune_recordings()
    check("within the slack: still removes nothing", removed == 0,
          f"{removed} -- pruning on every frame would rewrite the whole index "
          f"each time")

# ===========================================================================
section("over the threshold, the OLDEST go")

with Corpus(1200, keep=1000, slack=100) as c:
    removed = trade.prune_recordings()
    frames = c.frames()
    check("removed the excess", removed == 200, f"removed {removed}")
    check("kept exactly RECORD_KEEP", len(frames) == 1000, f"{len(frames)}")
    check("kept the NEWEST, not the oldest",
          frames[0] == "run_00201.png" and frames[-1] == "run_01200.png",
          f"{frames[0]} .. {frames[-1]} -- the newest frames match the current "
          f"build and the current shop; the oldest match neither")

# ===========================================================================
section("the index stays in step -- both directions")

with Corpus(1300, keep=1000, slack=100) as c:
    trade.prune_recordings()
    frames, indexed = set(c.frames()), set(c.indexed())
    check("no index line points at a deleted frame",
          not (indexed - frames),
          f"{len(indexed - frames)} stale line(s) -- these make the corpus "
          f"suite assert against files that are not there")
    check("no frame is left without an index line",
          not (frames - indexed),
          f"{len(frames - indexed)} orphan(s) -- a frame no test can interpret")
    check("the index has exactly the surviving frames",
          len(indexed) == 1000, f"{len(indexed)}")

# ===========================================================================
section("ordering is by sequence number, not mtime")

with Corpus(1200, keep=1000, slack=100) as c:
    # Touch an OLD frame so its mtime is the newest in the directory. Ordering
    # by mtime would spare it and delete something newer; a copy or a restore
    # rewrites mtimes wholesale, so it cannot be the authority on age.
    old = c.dir / "run_00005.png"
    old.write_bytes(b"touched")
    trade.prune_recordings()
    check("a freshly-touched OLD frame is still pruned",
          not old.exists(),
          "mtime is whatever the filesystem last recorded; the sequence "
          "number is what actually says which frame came first")

# ===========================================================================
section("pruning never breaks a run")

with Corpus(1200, keep=1000, slack=100) as c:
    # An unparseable index line must not lose the whole index.
    with (c.dir / "run_index.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("{not json at all\n")
    removed = trade.prune_recordings()
    check("a corrupt index line is survived", removed > 0, f"{removed}")
    check("...and kept rather than silently dropped",
          any("not json" in l for l in
              (c.dir / "run_index.jsonl").read_text(encoding="utf-8").splitlines()),
          "an unreadable line is evidence too; dropping it hides whatever "
          "wrote it")

saved_dir = trade.RECORD_DIR
try:
    trade.RECORD_DIR = Path("Z:/does/not/exist/at/all")
    removed = trade.prune_recordings()
    check("a missing corpus directory returns 0 rather than raising",
          removed == 0, f"{removed}")
finally:
    trade.RECORD_DIR = saved_dir

# ===========================================================================
section("record() prunes as it writes")

with Corpus(1200, keep=1000, slack=100) as c:
    from PIL import Image
    saved_enabled = trade.RECORD_ENABLED
    try:
        trade.RECORD_ENABLED = True
        trade._record_seq = 0          # forces the highest-number bootstrap
        trade.record("t.frame", Image.new("RGB", (4, 4)))
        frames = c.frames()
        check("recording continues past the old hard stop",
              "run_01201.png" in frames,
              f"newest is {frames[-1]} -- a 494-cycle run once produced NO "
              f"frames because recording had stopped")
        check("...and the window is still capped", len(frames) <= 1001,
              f"{len(frames)} frames kept")
        check("the new frame is indexed", "run_01201.png" in c.indexed(),
              "a frame with no index line is an orphan")
    finally:
        trade.RECORD_ENABLED = saved_enabled


check("the real corpus directory was never touched",
      trade.RECORD_DIR == trade.SCRIPT_DIR / "unit_tests" / "corpus",
      f"{trade.RECORD_DIR} -- this suite must not prune the live evidence")


raise SystemExit(summary())
