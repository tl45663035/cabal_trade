import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    failed = []
    for test in sorted(HERE.glob("*_test.py")):
        started = time.perf_counter()
        run = subprocess.run([sys.executable, str(test)], capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
        took = time.perf_counter() - started
        ok = run.returncode == 0
        print(f"{'pass' if ok else 'FAIL'}  {test.name:34} {took:6.1f}s")
        if not ok:
            failed.append(test.name)
            for line in (run.stdout + run.stderr).splitlines():
                if line.startswith("FAIL") or "Error" in line or "Traceback" in line:
                    print(f"      {line[:160]}")
    print(f"\n{len(failed)} of {len(list(HERE.glob('*_test.py')))} test file(s) failed"
          + (f": {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
