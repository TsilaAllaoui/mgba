#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BUILD="${1:-${BUILD_DIR:-$ROOT/build}}"
fail(){ echo "FAIL: $*" >&2; exit 1; }
pass(){ echo "PASS: $*"; }

echo "mGBA + GBAVirtualCart qualification"

[[ -f "$ROOT/src/gba/CMakeLists.txt" ]] || fail "src/gba/CMakeLists.txt missing"
grep -q 'cart/gbabr.c' "$ROOT/src/gba/CMakeLists.txt" || fail "cart/gbabr.c is not in GBA source list"
pass "gbabr.c is compiled into libmgba"

SELFTEST="$BUILD/mgba-gbavc-selftest"
[[ -x "$SELFTEST" ]] || SELFTEST="$(find "$BUILD" -type f -name mgba-gbavc-selftest -perm -111 -print -quit)"
[[ -n "$SELFTEST" && -x "$SELFTEST" ]] || fail "mgba-gbavc-selftest not found"
"$SELFTEST" | tee "$BUILD/mgba-gbavc-selftest.log"
grep -q 'SELFTEST RESULT: PASS (0 failures)' "$BUILD/mgba-gbavc-selftest.log" || fail "mGBA GBAVirtualCart selftest"
pass "mGBA bus/protocol selftest"

LIB="$(find "$BUILD" -maxdepth 3 -type f \( -name 'libmgba.so' -o -name 'libmgba.so.*' \) -print | sort | tail -1)"
[[ -n "$LIB" ]] || fail "shared libmgba.so not found"
SYMS="$BUILD/libmgba-defined-symbols.txt"
nm -D --defined-only "$LIB" > "$SYMS"
for sym in \
  GBAGBABRResetVolatile GBAGBABRDestroy GBAGBABRNoteNativeSaveAccess \
  GBAGBABRWriteSRAM8 GBAGBABRRecordKeys GBAGBABRLoadExtraState \
  GBAGBABRReadROM16 GBAGBABRReadROM32 GBAGBABRSaveExtraState \
  GBAGBABRTryActivate GBAGBABRWriteROM16 GBAGBABRReadSRAM8; do
  grep -qE "[[:space:]]${sym}$" "$SYMS" || fail "$sym is not defined by libmgba.so"
done
pass "all GBAGBABR symbols are defined in libmgba.so"

python3 -m py_compile "$ROOT"/tools/mgba/*.py "$ROOT"/tools/gbabr/*.py
pass "Python tools compile"

OLD="GBA""SaveAnalyzer"
OLDLOW="gba""saveanalyzer"
if grep -RIn --exclude-dir='__pycache__' -E "${OLD}|${OLDLOW}" \
  "$ROOT/tools/mgba" "$ROOT/mgba.sh" "$ROOT/src/gba/cart/gbabr.c" \
  "$ROOT/include/mgba/internal/gba/cart/gbabr.h" "$ROOT/src/platform/gbabr-autoqa.c" \
  "$ROOT/src/platform/gbabr-selftest.c" "$ROOT/src/platform/qt/CoreController.cpp" \
  "$ROOT/src/platform/qt/CoreController.h" "$ROOT/src/platform/qt/Window.cpp"; then
  fail "old analyzer branding remains"
fi
pass "branding cleaned to mGBA"

echo "mGBA QUALIFICATION PASS"
