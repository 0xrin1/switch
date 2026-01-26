#!/usr/bin/env bash
set -euo pipefail

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not found; install Python 3 first" >&2
  exit 1
fi

VENV_DIR=".venv-precommit"

if [ ! -d "$VENV_DIR" ]; then
  if ! python3 -m venv "$VENV_DIR" >/dev/null 2>&1; then
    echo "failed to create venv; on Debian/Ubuntu you may need: sudo apt-get install python3-venv" >&2
    exit 1
  fi
fi

"$VENV_DIR/bin/pip" install -U pip pre-commit >/dev/null
"$VENV_DIR/bin/pre-commit" install

echo "pre-commit installed" >&2
