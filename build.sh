#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="${BUILD_DIR:-$ROOT/build}"
JOBS="${JOBS:-$(nproc 2>/dev/null || echo 4)}"
[[ "$JOBS" =~ ^[0-9]+$ ]] || JOBS=4
(( JOBS > 12 )) && JOBS=12

CLEAN=0
HEADLESS=0
NO_UPDATE=0

for a in "$@"; do
  case "$a" in
    --clean) CLEAN=1 ;;
    --headless) HEADLESS=1 ;;
    --no-update) NO_UPDATE=1 ;;
    -h|--help)
      cat <<'USAGE'
Usage: ./build.sh [--clean] [--headless] [--no-update]

Default behavior:
  - update GBAVirtualCart submodule to latest origin/master
  - build/test GBAVirtualCart
  - build SDL + Qt + headless mGBA frontends/tools
  - run qualification

Options:
  --clean      remove the mGBA build directory first
  --headless   build only headless + virtual-cart tools
  --no-update  do not fetch/update GBAVirtualCart before building
USAGE
      exit 0
      ;;
    *)
      echo "ERROR: unknown option: $a" >&2
      exit 2
      ;;
  esac
done

[[ $CLEAN -eq 1 ]] && rm -rf "$BUILD"

if [[ ! -f "$ROOT/externals/GBAVirtualCart/CMakeLists.txt" ]]; then
  if [[ -f "$ROOT/.gitmodules" ]]; then
    git -C "$ROOT" submodule update --init --recursive externals/GBAVirtualCart || true
  fi
fi

[[ -f "$ROOT/externals/GBAVirtualCart/CMakeLists.txt" ]] || {
  echo "ERROR: GBAVirtualCart submodule is missing. Run ./setup.sh first." >&2
  exit 2
}

for x in git cmake ninja c++ python3; do
  command -v "$x" >/dev/null || {
    echo "ERROR: missing '$x'. Run ./setup.sh." >&2
    exit 2
  }
done

exec > >(tee "$ROOT/mgba-build.log") 2>&1

echo "============================================================"
echo " mGBA + GBAVirtualCart build"
echo "============================================================"
echo "Root : $ROOT"
echo "Build: $BUILD"
echo

if [[ $NO_UPDATE -eq 0 ]]; then
  echo "[0/3] Updating GBAVirtualCart to latest origin/master..."
  git -C "$ROOT/externals/GBAVirtualCart" fetch origin master --prune
  git -C "$ROOT/externals/GBAVirtualCart" checkout -B master origin/master
  git -C "$ROOT/externals/GBAVirtualCart" reset --hard origin/master
  echo "GBAVirtualCart: $(git -C "$ROOT/externals/GBAVirtualCart" rev-parse --short HEAD)"
  echo
else
  echo "[0/3] GBAVirtualCart update skipped (--no-update)."
  echo
fi

echo "[1/3] GBAVirtualCart"
if [[ -x "$ROOT/externals/GBAVirtualCart/build.sh" ]]; then
  "$ROOT/externals/GBAVirtualCart/build.sh"
else
  cmake -S "$ROOT/externals/GBAVirtualCart" \
        -B "$BUILD/gbavc-standalone" \
        -G Ninja \
        -DGBAVC_BUILD_TESTS=ON
  cmake --build "$BUILD/gbavc-standalone" -j "$JOBS"
  ctest --test-dir "$BUILD/gbavc-standalone" --output-on-failure
fi

echo "[2/3] mGBA"
QT=ON
SDL=ON
if [[ $HEADLESS -eq 1 ]]; then
  QT=OFF
  SDL=OFF
fi

cmake -S "$ROOT" -B "$BUILD" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_QT="$QT" \
  -DFORCE_QT_VERSION=6 \
  -DBUILD_SDL="$SDL" \
  -DBUILD_HEADLESS=ON \
  -DBUILD_GBAVC_TOOLS=ON \
  -DBUILD_SHARED=ON \
  -DBUILD_STATIC=OFF \
  -DUSE_FFMPEG=OFF \
  -DUSE_LIBZIP=OFF \
  -DUSE_ELF=OFF \
  -DUSE_LUA=OFF \
  -DUSE_EDITLINE=OFF \
  -DSKIP_GIT=ON

targets=(mgba-headless mgba-autoqa mgba-gbavc-selftest)
if [[ $HEADLESS -eq 0 ]]; then
  targets+=(mgba-sdl mgba-qt)
fi
cmake --build "$BUILD" --target "${targets[@]}" -j "$JOBS"

# Stable convenience launchers at repository root.
find_bin() {
  local name="$1"
  find "$BUILD" -maxdepth 5 -type f -name "$name" -perm -111 -print -quit
}

for name in mgba-headless mgba-sdl mgba-qt; do
  p="$(find_bin "$name" || true)"
  [[ -n "$p" ]] && ln -sfn "$p" "$ROOT/$name"
done

echo "[3/3] Qualification"
/home/tsila/gba/mgba/tools/mgba/test.sh
"$ROOT/tools/mgba/test.sh" "$BUILD"

echo
echo "Build complete."
echo "GBAVirtualCart master: $(git -C "$ROOT/externals/GBAVirtualCart" rev-parse --short HEAD)"
[[ -e "$ROOT/mgba-sdl" ]] && echo "  SDL : ./mgba-sdl"
[[ -e "$ROOT/mgba-qt" ]] && echo "  Qt  : ./mgba-qt"
echo "  Menu: ./mgba.sh"
