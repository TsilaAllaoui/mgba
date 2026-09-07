#!/usr/bin/env python3
"""One-command deterministic AutoQA wrapper for mGBA-GBABR."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from gbabr_common import default_workdir, environment_for_manifest, find_executable, find_manifest, load_manifest


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run a patched ROM against its virtual GBABR cartridge with strict automated checks."
    )
    ap.add_argument("rom")
    ap.add_argument("--manifest")
    ap.add_argument("--workdir")
    ap.add_argument("--fresh", action="store_true", help="erase virtual NOR/FRAM before this run")
    ap.add_argument("--frames", type=int, default=18000)
    ap.add_argument("--require", choices=["program", "erase", "fram", "save", "any"])
    ap.add_argument("--stop-on", choices=["program", "erase", "fram", "save", "any"])
    ap.add_argument("--after-event", type=int, default=60)
    ap.add_argument("--cold-reset-on-event", action="store_true")
    ap.add_argument("--state", help="load savestate before run; relative names are also searched in workdir")
    ap.add_argument("--movie", help="input movie file")
    ap.add_argument("--checkpoint", action="store_true", help="save rolling pre-event.ss and exact event.ss")
    ap.add_argument("--checkpoint-interval", type=int, default=60)
    ap.add_argument("--final-state", action="store_true", help="write final.ss")
    ap.add_argument("--quick-save", action="store_true", help="require + stop on first save activity")
    ap.add_argument("--cold-save", action="store_true", help="stop on first save, cold-reset while preserving cart, then continue")
    ap.add_argument("--autoqa", help="path to mgba-gbabr-autoqa")
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
        for name in ("cart.bin", "fram.bin", "events.jsonl", "report.json", "pre-event.ss", "event.ss", "final.ss"):
            p = workdir / name
            if p.exists():
                p.unlink()
    events = workdir / "events.jsonl"
    if events.exists():
        events.unlink()  # event trace is per-run; persistent cart is separate
    report = workdir / "report.json"

    if args.autoqa:
        autoqa = Path(args.autoqa).resolve()
    else:
        autoqa = find_executable("mgba-gbabr-autoqa", Path(__file__), "MGBA_GBABR_AUTOQA")
    if not autoqa or not autoqa.is_file():
        ap.error("mgba-gbabr-autoqa not found; put it in bin/, PATH, or set MGBA_GBABR_AUTOQA")

    require = args.require
    stop_on = args.stop_on
    cold = args.cold_reset_on_event
    if args.quick_save:
        require = "save"
        stop_on = "save"
    if args.cold_save:
        require = "save"
        stop_on = "save"
        cold = True

    cmd = [str(autoqa), str(rom), "--frames", str(args.frames), "--after-event", str(args.after_event), "--report", str(report)]
    if require:
        cmd += ["--require", require]
    if stop_on:
        cmd += ["--stop-on", stop_on]
    if cold:
        cmd += ["--cold-reset-on-event"]
    if args.state:
        state = Path(args.state)
        if not state.is_absolute() and not state.exists() and (workdir / state).exists():
            state = workdir / state
        cmd += ["--state", str(state.resolve())]
    if args.movie:
        cmd += ["--movie", str(Path(args.movie).resolve())]
    if args.checkpoint:
        cmd += [
            "--pre-event-state", str(workdir / "pre-event.ss"),
            "--checkpoint-interval", str(args.checkpoint_interval),
            "--event-state", str(workdir / "event.ss"),
        ]
    if args.final_state:
        cmd += ["--final-state", str(workdir / "final.ss")]

    env = environment_for_manifest(manifest, workdir, events_path=events)
    print(f"GBABR AutoQA: {rom.name}")
    print(f"  cart: {manifest['cart']['profile']}  offset: 0x{int(manifest['cart'].get('rom_offset', 0)):X}")
    print(f"  manifest: {manifest_path}")
    print(f"  workdir: {workdir}")
    print(f"  virtual cart: {'FRESH' if args.fresh else 'PERSISTENT/REUSED'}")
    sys.stdout.flush()
    result = subprocess.run(cmd, env=env)

    if report.is_file():
        try:
            r = json.loads(report.read_text(encoding="utf-8"))
            e = r.get("events", {})
            print(
                f"RESULT {r.get('result')}: {r.get('reason')} | "
                f"program={e.get('program', 0)} erase={e.get('erase', 0)} fram={e.get('fram', 0)} "
                f"illegal={e.get('illegal_nor', 0)} invalid={e.get('invalid_sequence', 0)} native={e.get('native_save', 0)}"
            )
        except Exception as e:
            print(f"WARNING: could not parse report: {e}", file=sys.stderr)
    print(f"Report: {report}")
    print(f"Events: {events}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
