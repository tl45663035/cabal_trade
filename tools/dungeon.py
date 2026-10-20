import subprocess
import sys
from pathlib import Path

DUNGEON = Path(__file__).resolve().parent.parent / "src_1080p" / "dungeon.py"

if __name__ == "__main__":
    sys.exit(subprocess.run([sys.executable, str(DUNGEON), *sys.argv[1:]],
                            cwd=DUNGEON.parent.parent).returncode)
