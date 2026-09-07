#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUILD="${BUILD_DIR:-$ROOT/build-mgba}"
echo 'mgba/mGBA v1.0 qualification'
[[ -x "$ROOT/mgba-SDL" ]] || { echo 'FAIL: SDL frontend missing; run ./build.sh'; exit 1; }
[[ -x "$BUILD/mgba-mgba-selftest" ]] || { echo 'FAIL: selftest missing; run ./build.sh'; exit 1; }
python3 -m py_compile "$ROOT"/tools/mgba/*.py
"$BUILD/mgba-mgba-selftest" | tee "$BUILD/selftest-v1.log"
grep -q 'SELFTEST RESULT: PASS (0 failures)' "$BUILD/selftest-v1.log"
python3 "$ROOT/tools/mgba/gsa_virtual_cart.py" --help >/dev/null
echo 'PASS: Python tools compile'
echo 'PASS: mGBA GBAVirtualCart bus selftest'
echo 'PASS: SDL interactive frontend built'
echo 'mgba/mGBA v1.0 QUALIFICATION PASS'
