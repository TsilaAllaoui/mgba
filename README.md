# mGBA + GBAVirtualCart fork v1.0

This repository overlay adds persistent virtual GBA flash-cartridge support to an mGBA fork by using **GBAVirtualCart as a Git submodule**. The same NOR/FRAM backing files can be opened by the matching melonDS fork, so a cart flashed in melonDS can be booted and tested in mGBA without converting or copying its contents.

The fork keeps the normal mGBA frontends. For interactive virtual-cart testing, **SDL is preferred** because it gives direct keyboard/gamepad input. Qt remains available for the normal GUI and checkpoint/movie recording workflow.

## What this adds

- Shared `GBAVirtualCart` cartridge core, used as `externals/GBAVirtualCart`.
- Persistent virtual NOR and FRAM backing files.
- Existing-cart mode: open a cart created/flashed by melonDS without resetting or overlaying it.
- S29GL01GS / Mini128 128 MiB NOR + FRAM profile.
- ST M36 16 MiB profile.
- M6/M6M, M6MGD137, M28W640FS-T70ZA6 and MX26L6420MC-90 profiles.
- S29 mapper/bank/FRAM behavior and NOR command emulation from the shared module.
- mGBA bus adapter with strict writable ranges, event statistics and persistent state.
- Compact savestate support for virtual-cart runtime state.
- Deterministic input movie/checkpoint helpers.
- AutoQA and a bus/protocol self-test.
- A simple menu launcher: `./mgba.sh`.
- SDL, Qt and headless frontends from one build.

## Important workflow rule

**Do not run melonDS and mGBA at the same time against the same writable virtual-cart backing files.** Close one emulator completely before opening the cart in the other.

## First-time setup

First publish your `GBAVirtualCart` repository and create the `v1.0.0` tag. By default the setup script expects:

```text
https://github.com/TsilaAllaoui/GBAVirtualCart.git
```

Then copy this overlay into the root of your fresh mGBA fork and run:

```bash
./setup.sh
```

That one command:

1. installs the Ubuntu/WSL build dependencies;
2. adds `externals/GBAVirtualCart` as a real Git submodule;
3. checks out `v1.0.0`;
4. builds GBAVirtualCart and runs its self-test;
5. builds mGBA SDL, Qt, headless, AutoQA and the GBAVirtualCart self-test;
6. verifies that the virtual-cart functions are actually linked into `libmgba.so`.

If your GBAVirtualCart fork uses another URL:

```bash
GBAVC_REPO=https://github.com/YOURNAME/GBAVirtualCart.git ./setup.sh
```

If you want another branch/tag/commit:

```bash
GBAVC_REF=my-branch ./setup.sh
```

If dependencies are already installed:

```bash
./setup.sh --no-apt
```

## Normal rebuild

```bash
./build.sh
```

Clean rebuild:

```bash
./build.sh --clean
```

Headless-only developer build:

```bash
./build.sh --clean --headless
```

The normal build creates convenient launchers at the repository root when the frontends are available:

```text
./mgba-sdl
./mgba-qt
./mgba-headless
./mgba.sh
```

## Main menu

Run:

```bash
./mgba.sh
```

Useful entries include:

- batch ROM/cart scan;
- one-ROM/one-cart test;
- AutoQA/regression;
- checkpoint/movie recording;
- boot an existing melonDS virtual cart;
- list shared GBAVirtualCart backings;
- run the mGBA/GBAVirtualCart qualification suite.

## Boot the same virtual cart used by melonDS

The menu can automatically reuse the `CART_DIR` saved by the matching melonDS launcher. You can also choose the cart folder manually in the mGBA menu.

For direct use:

```bash
python3 tools/mgba/mgba_virtual_cart.py --cart-dir /path/to/carts --profile s29
```

or:

```bash
python3 tools/mgba/mgba_virtual_cart.py --cart-dir /path/to/carts --profile m36
```

Interactive cart boot prefers `mgba-sdl`. Force Qt only when wanted:

```bash
python3 tools/mgba/mgba_virtual_cart.py --cart-dir /path/to/carts --profile s29 --qt
```

List detected backings:

```bash
python3 tools/mgba/mgba_virtual_cart.py --cart-dir /path/to/carts --list
```

## Persistent files

Typical backing names are supplied by GBAVirtualCart profiles, for example:

```text
mini128.nor
mini128.fram
m36.nor
m6m.nor
```

mGBA writes its own event trace next to the backing using a separate `.mgba.jsonl` name, so the melonDS trace is not overwritten.

The mGBA helper settings are stored separately from normal upstream mGBA configuration under:

```text
~/.config/mgba-gbavc/
```

## Qualification

After a build, run:

```bash
./tools/mgba/test.sh build
```

The test checks:

- `src/gba/CMakeLists.txt` actually includes `cart/gbabr.c`;
- mGBA bus/protocol self-test passes;
- every required `GBAGBABR*` function is defined by `libmgba.so`;
- Python helper tools compile;
- old development branding is absent from the fork-specific files.

The `libmgba.so` symbol check specifically prevents a previous packaging failure where `gbabr.c` was present in the overlay but missing from `src/gba/CMakeLists.txt`, which caused linker errors such as undefined references to `GBAGBABRTryActivate`, `GBAGBABRReadROM16`, `GBAGBABRDestroy`, and related functions.

## Current validation state

The cleaned v1.0 overlay was compiled in shared-library/headless mode with GBAVirtualCart v1.0.0:

```text
GBAVirtualCart native self-test: 27 PASS / 0 FAIL
mGBA bus/protocol self-test:     PASS / 0 failures
libmgba GBAGBABR symbol check:   PASS
Python helper compile:           PASS
```

M36 virtual-cart interoperability between melonDS and mGBA has been exercised successfully, including save persistence. S29/Mini128 single-cart boot/save has also been exercised after updating the cart menu.

### Known S29 follow-up

A more complex Mini128 multi-game sequence has exposed a remaining S29/shared-cart issue during repeated installs/save transitions. Keep that as a **GBAVirtualCart/S29 model follow-up**. Do not hide it with frontend-specific workarounds. Preserve failing `mini128.nor`/`mini128.fram` fixtures when reproducing it.

## Applying this overlay to an existing fork

If you already copied an older development overlay into your fork, use the included updater:

```bash
./APPLY_TO_FORK.sh ~/path/to/your/mgba
cd ~/path/to/your/mgba
./setup.sh
```

It removes the obsolete development-branded launcher/tool directory, copies the corrected files (including the previously omitted `src/gba/CMakeLists.txt` and Qt controller header), and then the normal setup performs a clean build.

## Git commit

After setup succeeds:

```bash
git status
git add .
git commit -m "Add GBAVirtualCart integration v1.0"
git push
```

`externals/GBAVirtualCart` should appear as a **Git submodule pointer**, not as duplicated source files.

## Upstream

This is a fork of the mGBA project. mGBA remains under its upstream Mozilla Public License 2.0 terms. GBAVirtualCart is maintained separately as the shared cartridge-emulation module used by both emulator forks.
