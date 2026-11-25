#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

usable_python() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1
}

# Windows/Git Bash sometimes exposes a python3 App Execution Alias that opens
# the Store instead of running Python, so test the interpreter rather than just
# checking for its name. macOS and most Linux distributions provide python3;
# python is the fallback for environments that only expose that name.
if command -v python3 >/dev/null 2>&1 && usable_python python3; then
  exec python3 scripts/run.py "$@"
fi

if command -v python >/dev/null 2>&1 && usable_python python; then
  exec python scripts/run.py "$@"
fi

echo "[job-signal] error: Python 3.12+ is required." >&2
exit 1
