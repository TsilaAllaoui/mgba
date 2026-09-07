#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GBAVC_REPO="${GBAVC_REPO:-https://github.com/TsilaAllaoui/GBAVirtualCart.git}"
NO_APT=0

for a in "$@"; do
  case "$a" in
    --no-apt) NO_APT=1 ;;
    -h|--help)
      cat <<'USAGE'
Usage: ./setup.sh [--no-apt]

One-command first-time setup for this mGBA fork:
  1. installs Ubuntu/WSL build dependencies
  2. adds externals/GBAVirtualCart as a Git submodule if needed
  3. updates GBAVirtualCart to the latest origin/master
  4. performs a clean mGBA build and qualification

Optional environment override:
  GBAVC_REPO=<git-url>   GBAVirtualCart repository URL

Examples:
  ./setup.sh
  ./setup.sh --no-apt
  GBAVC_REPO=https://github.com/you/GBAVirtualCart.git ./setup.sh
USAGE
      exit 0
      ;;
    *)
      echo "ERROR: unknown option: $a" >&2
      exit 2
      ;;
  esac
done

cd "$ROOT"

if ! git rev-parse --show-toplevel >/dev/null 2>&1; then
  echo "ERROR: setup.sh must be run from inside your cloned mGBA fork." >&2
  exit 2
fi

if [[ $NO_APT -eq 0 ]] && command -v apt-get >/dev/null 2>&1; then
  echo "[1/3] Installing build dependencies..."
  sudo apt-get update
  sudo apt-get install -y \
    build-essential cmake ninja-build pkg-config git python3 \
    qt6-base-dev qt6-multimedia-dev libsdl2-dev \
    libepoxy-dev libgl1-mesa-dev libpng-dev zlib1g-dev \
    libminizip-dev libjson-c-dev
else
  echo "[1/3] Dependency installation skipped."
fi

echo "[2/3] Setting up latest GBAVirtualCart master..."
mkdir -p externals

if git ls-files --stage externals/GBAVirtualCart 2>/dev/null | grep -q '^160000 '; then
  # Existing real submodule: keep its configured URL unless GBAVC_REPO was
  # explicitly supplied, then point it at that repository.
  if [[ -n "${GBAVC_REPO:-}" ]]; then
    git config -f .gitmodules submodule.externals/GBAVirtualCart.url "$GBAVC_REPO"
  fi
  git submodule sync -- externals/GBAVirtualCart
  git submodule update --init --recursive externals/GBAVirtualCart
else
  if [[ -e externals/GBAVirtualCart ]] && [[ -n "$(ls -A externals/GBAVirtualCart 2>/dev/null || true)" ]]; then
    echo "ERROR: externals/GBAVirtualCart exists but is not a Git submodule." >&2
    echo "Move/remove it, then rerun ./setup.sh." >&2
    exit 2
  fi
  rm -rf externals/GBAVirtualCart
  git submodule add -b master "$GBAVC_REPO" externals/GBAVirtualCart
fi

# Always use the newest commit currently on origin/master.
git -C externals/GBAVirtualCart fetch origin master --prune
git -C externals/GBAVirtualCart checkout -B master origin/master
git -C externals/GBAVirtualCart reset --hard origin/master

git config -f .gitmodules submodule.externals/GBAVirtualCart.branch master
git add .gitmodules externals/GBAVirtualCart 2>/dev/null || true

for line in '/build/' '/build-headless/' '/mgba-sdl' '/mgba-qt' '/mgba-headless' '/mgba-build.log'; do
  grep -qxF "$line" .gitignore 2>/dev/null || echo "$line" >> .gitignore
done

echo "GBAVirtualCart: $(git -C externals/GBAVirtualCart rev-parse --short HEAD) on master"

echo "[3/3] Building mGBA..."
chmod +x "$ROOT/build.sh"
exec "$ROOT/build.sh" --clean
