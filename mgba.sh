#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

find_exec() {
  local n p
  for n in "$@"; do
    for p in "$ROOT/$n" "$ROOT/build/$n" "$ROOT/build/sdl/$n" "$ROOT/build/qt/$n"; do
      [[ -x "$p" ]] && { printf '%s\n' "$p"; return 0; }
    done
  done
  return 1
}

: "${MGBA_AUTOQA:=$(find_exec mgba-autoqa mgba-gbabr-autoqa || true)}"
: "${MGBA_SELFTEST:=$(find_exec mgba-gbavc-selftest mgba-gbabr-selftest || true)}"
: "${MGBA_QT:=$(find_exec mgba-qt || true)}"
export MGBA_AUTOQA MGBA_SELFTEST MGBA_QT

exec python3 "$ROOT/tools/mgba/mgba.py" "$@"
