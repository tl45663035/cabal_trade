#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

command -v py >/dev/null 2>&1 || {
  echo "the py launcher is not installed; install Python from python.org and run this again"
  exit 1
}

setting() {
  py -c "import json, sys; print(json.load(open('config.json', encoding='utf-8'))['ocr'][sys.argv[1]])" "$1"
}

VERSION=$(setting backup_python_version)
PYTHON=$(setting backup_python)
VENV=$(dirname "$(dirname "$PYTHON")")

echo "PaddleOCR backup reader setup"
echo "environment: $VENV"
if [ -e "$PYTHON" ] && "$PYTHON" -c "" >/dev/null 2>&1; then
  echo "   already there: $("$PYTHON" --version)"
elif [ -e "$VENV" ]; then
  echo "   $VENV is there but its Python does not start; delete $VENV and run this again"
  exit 1
else
  if ! py -"$VERSION" -c "" >/dev/null 2>&1; then
    command -v winget >/dev/null 2>&1 || {
      echo "   Python $VERSION is not installed and winget is not available;"
      echo "   install Python $VERSION (64-bit) from python.org and run this again"
      exit 1
    }
    echo "   Python $VERSION is not installed; installing it with winget"
    winget install --id "Python.Python.$VERSION" --exact --scope user --silent \
      --accept-package-agreements --accept-source-agreements
    py -"$VERSION" -c "" >/dev/null 2>&1 || {
      echo "   Python $VERSION is still not found; open a new Git Bash and run this again"
      exit 1
    }
  fi
  echo "   making it with $(py -"$VERSION" --version)"
  py -"$VERSION" -m venv "$VENV"
fi

"$PYTHON" setup_paddle.py
