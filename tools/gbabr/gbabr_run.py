#!/usr/bin/env python3
"""Launch a GUI build of the mGBA-GBABR fork with the correct virtual cart."""
from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from gbabr_common import default_workdir, environment_for_manifest, find_manifest, load_manifest


def main() -> int:
    ap = argparse.ArgumentParser(description="Launch mGBA-GBABR GUI with manifest-configured virtual NOR/FRAM.")
    ap.add_argument("rom")
    ap.add_argument("--manifest")
    ap.add_argument("--workdir")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--emulator", help="path to a Qt/SDL executable built from this fork")
    ap.add_argument("emulator_args", nargs=argparse.REMAINDER)
    args = ap.parse_args()

    rom = Path(args.rom).resolve()
    if not rom.is_file():
        ap.error(f"ROM not found: {rom}")
    try:
        manifest_path = find_manifest(rom, args.manifest)
        manifest = load_manifest(manifest_path, rom)
    except Exception as e:
        ap.error(str(e))
    workdir = Path(args.workdir).resolve() if args.workdir else default_workdir(rom, manifest)
    workdir.mkdir(parents=True, exist_ok=True)
    if args.fresh:
        for name in ("cart.bin", "fram.bin", "events-gui.jsonl"):
            p = workdir / name
            if p.exists():
                p.unlink()

    candidates = []
    if args.emulator:
        candidates.append(Path(args.emulator))
    if os.environ.get("MGBA_GBABR_GUI"):
        candidates.append(Path(os.environ["MGBA_GBABR_GUI"]))
    here = Path(__file__).resolve().parent
    candidates += [here.parent / "bin" / "mgba-qt", here.parent.parent / "bin" / "mgba-qt"]
    import shutil
    for name in ("mgba-qt", "mgba-sdl"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    emulator = next((p.resolve() for p in candidates if p.is_file() and os.access(p, os.X_OK)), None)
    if not emulator:
        ap.error(
            "no GUI executable from this fork found. Build mgba-qt from the handoff source, "
            "then pass --emulator /path/to/mgba-qt or set MGBA_GBABR_GUI."
        )

    events = workdir / "events-gui.jsonl"
    if events.exists():
        events.unlink()
    env = environment_for_manifest(manifest, workdir, events_path=events)
    print(f"Launching {emulator.name}: {rom.name}")
    print(f"Virtual cart persists in: {workdir}")
    return subprocess.call([str(emulator), str(rom), *args.emulator_args], env=env)


if __name__ == "__main__":
    raise SystemExit(main())
