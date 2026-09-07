#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD="${BUILD_DIR:-$ROOT/build-r642-headless}"
SELFTEST="${mgba_SELFTEST:-$BUILD/mgba-mgba-selftest}"
AUTOQA="${mgba_AUTOQA:-$BUILD/mgba-mgba-autoqa}"

printf '\nmgba R6.4.2 / GBAVirtualCart qualification\n'
printf 'Source: %s\nBuild:  %s\n\n' "$ROOT" "$BUILD"

if [ ! -x "$SELFTEST" ] || [ ! -x "$AUTOQA" ]; then
  echo 'Headless validation binaries are missing; building them now...'
  cmake -S "$ROOT" -B "$BUILD" \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_QT=OFF -DBUILD_SDL=OFF -DBUILD_HEADLESS=ON \
    -DBUILD_SHARED=OFF -DBUILD_STATIC=ON \
    -DUSE_FFMPEG=OFF -DENABLE_LTO=OFF -DSKIP_GIT=ON
  cmake --build "$BUILD" --target mgba-mgba-selftest mgba-mgba-autoqa -j "${JOBS:-2}"
fi

printf '%s\n' '--- 1/6 shared GBAVirtualCart native selftest ---'
GBAVC_TEST="$ROOT/externals/GBAVirtualCart/build/gbavc-selftest"
if [ ! -x "$GBAVC_TEST" ]; then
  cmake -S "$ROOT/externals/GBAVirtualCart" -B "$ROOT/externals/GBAVirtualCart/build" -DGBAVC_BUILD_TESTS=ON -DCMAKE_BUILD_TYPE=Release >/dev/null
  cmake --build "$ROOT/externals/GBAVirtualCart/build" -j "${JOBS:-2}" >/dev/null
fi
"$GBAVC_TEST"

printf '%s\n' '--- 2/6 mGBA bus-level integration selftest ---'
"$SELFTEST"

printf '%s\n' '--- 3/6 Python automation compile ---'
python3 -m py_compile "$ROOT"/tools/mgba/*.py "$ROOT"/tools/gbabr/*.py
echo 'PY_COMPILE PASS'

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

printf '%s\n' '--- 4/6 AutoQA generated-ROM smoke ---'
python3 - "$TMP/minimal.gba" <<'PY'
import sys
p=sys.argv[1]
b=bytearray(1024)
b[0:4]=bytes.fromhex('FEFFFFEA')
b[0xA0:0xAC]=b'GBAVC SMOKE '
b[0xAC:0xB0]=b'TST0'
b[0xB0:0xB2]=b'01'
open(p,'wb').write(b)
PY
MGBA_GBAVC_PROFILE=m6m \
MGBA_GBAVC_NOR="$TMP/autoqa-m6m.nor" \
MGBA_GBAVC_EVENTS="$TMP/autoqa.events.jsonl" \
MGBA_GBAVC_STRICT=0 \
"$AUTOQA" "$TMP/minimal.gba" --frames 60 --report "$TMP/autoqa-report.json"
grep -q '"result": "PASS"' "$TMP/autoqa-report.json"
echo 'AUTOQA_SMOKE PASS'

printf '%s\n' '--- 5/6 melonDS-style existing-cart preparation ---'
python3 - "$ROOT" "$TMP/carts" <<'PY'
import json,sys
from pathlib import Path
root=Path(sys.argv[1]); out=Path(sys.argv[2]); out.mkdir(parents=True,exist_ok=True)
d=json.load(open(root/'externals/GBAVirtualCart/profiles/builtin_profiles.json'))
for p in d['profiles']:
    if p['key'] not in {'s29','m36','m6m'}: continue
    nor=out/p['default_nor_filename']
    with nor.open('wb') as f:
        f.write(bytes.fromhex('FEFFFFEA'))
        f.seek(int(p['capacity_bytes'])-1); f.write(b'\xFF')
    fb=int(p.get('fram_bytes',0) or 0)
    if fb:
        fram=out/p['default_fram_filename']
        with fram.open('wb') as f:
            f.seek(fb-1); f.write(b'\xFF')
PY
for p in s29 m36 m6; do
  python3 "$ROOT/tools/mgba/gsa_virtual_cart.py" \
    --cart-dir "$TMP/carts" --profile "$p" --gui "$AUTOQA" \
    --prepare-only --no-save-folder | tee "$TMP/prepare-$p.txt"
  grep -q 'mode    : EXISTING (no reset, no ROM overlay)' "$TMP/prepare-$p.txt"
done
python3 "$ROOT/tools/mgba/gsa_virtual_cart.py" --cart-dir "$TMP/carts" --list | tee "$TMP/list.txt"
grep -q 'READY s29' "$TMP/list.txt"
grep -q 'READY m36' "$TMP/list.txt"
grep -q 'READY m6m' "$TMP/list.txt"
echo 'EXISTING_CART_PREP PASS'

printf '%s\n' '--- 6/6 interactive keyboard/controller config ---'
"$ROOT/tools/mgba/test_r642_input.sh"

cat <<'EOT'

============================================================
R6.4.2 QUALIFICATION PASS
- GBAVirtualCart native model
- mGBA adapter/selftest
- Python automation
- AutoQA generated-ROM smoke
- melonDS-style S29/M36/M6 existing-cart preparation
- persistent interactive keyboard/controller config
- SDL2 controller backend build policy
============================================================
EOT
