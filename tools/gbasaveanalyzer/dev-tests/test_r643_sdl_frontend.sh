#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TMP="$ROOT/build-r643-sdl-discovery-test"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/sdl" "$TMP/qt"
printf '#!/bin/sh\nexit 0\n' > "$TMP/sdl/mgba-sdl"
printf '#!/bin/sh\nexit 0\n' > "$TMP/qt/mgba-qt"
chmod +x "$TMP/sdl/mgba-sdl" "$TMP/qt/mgba-qt"
ROOT="$ROOT" python3 - <<'PY'
import os,sys
from pathlib import Path
root=Path(os.environ['ROOT'])
sys.path.insert(0,str(root/'tools/mgba'))
import gsa_virtual_cart
p=gsa_virtual_cart._frontend(None)
assert p.name=='mgba-sdl', p
assert 'sdl' in str(p.parent), p
print('PASS: existing-cart interactive frontend prefers SDL')
PY
grep -q 'mgba-sdl' "$ROOT/tools/mgba/build_qt_wsl.sh"
grep -q 'mgba-SDL' "$ROOT/tools/mgba/build_qt_wsl.sh"
echo 'PASS: WSL build creates stable mgba-SDL launcher'
python3 -m py_compile "$ROOT/tools/mgba/gsa_virtual_cart.py" "$ROOT/tools/mgba/gsa_common.py"
echo 'PASS: Python launcher compiles'
echo 'R6.4.3 SDL FRONTEND QUALIFICATION PASS'
