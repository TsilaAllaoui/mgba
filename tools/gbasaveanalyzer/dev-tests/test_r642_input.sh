#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

printf '\nmgba R6.4.2 input/config qualification\n'

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "PASS: $*"; }

grep -q -- '-DBUILD_SDL=ON' tools/mgba/build_qt_wsl.sh || fail 'Qt build does not enable SDL'
grep -q 'libsdl2-dev' tools/mgba/build_qt_wsl.sh || fail 'Qt build dependency list does not include libsdl2-dev'
pass 'Qt build enables SDL2 controller backend'

grep -q 'if (!hasKeyboardBindings())' src/platform/qt/InputController.cpp || fail 'all--1 keyboard recovery guard missing'
grep -q 'bindKeyboardDefaults();' src/platform/qt/InputController.cpp || fail 'keyboard defaults helper missing'
for key in 'Qt::Key_X' 'Qt::Key_Z' 'Qt::Key_A' 'Qt::Key_S' 'Qt::Key_Return' 'Qt::Key_Backspace' 'Qt::Key_Up' 'Qt::Key_Down' 'Qt::Key_Left' 'Qt::Key_Right'; do
  grep -q "$key" src/platform/qt/InputController.cpp || fail "default keyboard binding missing: $key"
done
pass 'keyboard all--1 recovery and standard defaults present'

grep -q 'qt-isolated' tools/mgba/gsa_record.py || fail 'recorder isolation missing'
grep -q 'default_interactive_mgba_config_home' tools/mgba/gsa_virtual_cart.py || fail 'interactive persistent config helper missing'
pass 'recorder remains isolated while interactive cart boot is persistent'

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
HOME="$TMP/home"; export HOME
mkdir -p "$HOME"
export PYTHONPATH="$ROOT/tools/mgba"

python3 - <<'PYTEST'
import os
from pathlib import Path
from gsa_common import default_interactive_mgba_config_home
from gsa_virtual_cart import build_env
p={'key':'m6m','default_nor_filename':'m6m.nor','default_fram_filename':'','default_events_filename':'m6m.events.jsonl'}
cart=Path(os.environ['HOME'])/'carts'; cart.mkdir(parents=True)
(cart/'m6m.nor').write_bytes(b'\xff')
events=cart/'m6m.mgba.jsonl'
base=default_interactive_mgba_config_home()
env=build_env(cart,p,events,config_home=base,use_system_config=False)
assert Path(env['XDG_CONFIG_HOME']) == base/'config'
assert Path(env['XDG_DATA_HOME']) == base/'data'
assert Path(env['XDG_CACHE_HOME']) == base/'cache'
assert env['mgba_INTERACTIVE_MGBA_CONFIG'] == str(base)
assert 'qt-isolated' not in env['XDG_CONFIG_HOME']
os.environ['XDG_CONFIG_HOME']='/tmp/gsa-r642-system-config'
env2=build_env(cart,p,events,config_home=base,use_system_config=True)
assert env2['XDG_CONFIG_HOME']=='/tmp/gsa-r642-system-config'
print('PY_INPUT_ENV_PASS')
PYTEST
pass 'interactive XDG config is persistent and system override works'

mkdir -p "$TMP/carts"
python3 - "$TMP/carts/m6m.nor" <<'PYMAKE'
from pathlib import Path
import sys
p=Path(sys.argv[1])
size=8*1024*1024
with p.open('wb') as f:
    f.write(b'\x00\x00\x00\xea')
    f.write(b'\x00'*(0xC0-4))
    f.seek(size-1); f.write(b'\xff')
PYMAKE
OUT="$TMP/prepare.txt"
python3 tools/mgba/gsa_virtual_cart.py \
  --cart-dir "$TMP/carts" --profile m6 --gui /bin/true --prepare-only >"$OUT"
grep -q 'mode    : EXISTING' "$OUT" || fail 'existing-cart mode not selected'
grep -q 'persistent interactive' "$OUT" || fail 'persistent interactive config not reported'
grep -q "$HOME/.config/mgba/mgba-user" "$OUT" || fail 'expected persistent config path not reported'
pass 'existing-cart prepare uses dedicated persistent input config'

printf '\nR6.4.2 INPUT QUALIFICATION PASS\n'
