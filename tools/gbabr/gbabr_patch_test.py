#!/usr/bin/env python3
"""Patch a ROM with GBASaveHandler, create a GBABR manifest, then AutoQA it."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from gbabr_common import find_executable


def main() -> int:
    ap = argparse.ArgumentParser(
        description="One command: libgbasave patch -> strict virtual-cart manifest -> mGBA-GBABR smoke/save test."
    )
    ap.add_argument("rom", help="vanilla input .gba")
    ap.add_argument("target", help="GBASaveHandler NOR target, e.g. m6m,m28w640fs-t70za6,m36,m6mgd137,mx26l6420mc-90")
    ap.add_argument("--patcher", help="GBASaveHandler executable")
    ap.add_argument("--output", help="patched ROM output")
    ap.add_argument("--frames", type=int, default=900, help="smoke-test frames (default 900 = ~15 sec emulated time)")
    ap.add_argument("--save", action="store_true", help="require first save event (use with --state/--movie when needed)")
    ap.add_argument("--cold-save", action="store_true", help="cold-reset after first save event and continue")
    ap.add_argument("--state")
    ap.add_argument("--movie")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--rom-offset", default="0", help="physical ROM offset; normally 0 for fixed NOR carts")
    ap.add_argument("patcher_args", nargs=argparse.REMAINDER, help="extra GBASaveHandler arguments after --")
    args = ap.parse_args()

    src = Path(args.rom).resolve()
    if not src.is_file():
        ap.error(f"ROM not found: {src}")
    if args.patcher:
        patcher = Path(args.patcher).resolve()
    else:
        patcher = find_executable("GBASaveHandler-linux-x86_64-static", Path(__file__), "GBASAVEHANDLER")
        if not patcher:
            patcher = find_executable("GBASaveHandler", Path(__file__), "GBASAVEHANDLER")
    if not patcher or not patcher.is_file():
        ap.error("GBASaveHandler not found; pass --patcher or set GBASAVEHANDLER")

    out = Path(args.output).resolve() if args.output else src.with_name(f"{src.stem}-{args.target}.gbabr.gba")
    report = out.with_suffix(".patch.txt")
    manifest = out.with_suffix(".gbabr.json")

    extra = list(args.patcher_args)
    if extra and extra[0] == "--":
        extra = extra[1:]
    patch_cmd = [str(patcher), str(src), str(out), args.target, *extra]
    print("[1/3] Patching with libgbasave/GBASaveHandler...")
    proc = subprocess.run(patch_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    report.write_text(proc.stdout, encoding="utf-8")
    print(proc.stdout, end="")
    if proc.returncode != 0:
        print(f"Patch FAILED. Report: {report}", file=sys.stderr)
        return proc.returncode

    print("[2/3] Creating strict GBABR virtual-cart manifest...")
    manifest_tool = Path(__file__).with_name("gbabr_manifest.py")
    rc = subprocess.call([
        sys.executable, str(manifest_tool), str(out), "--report", str(report),
        "--rom-offset", args.rom_offset, "-o", str(manifest),
    ])
    if rc:
        return rc

    print("[3/3] Running mGBA-GBABR AutoQA...")
    test_tool = Path(__file__).with_name("gbabr_test.py")
    test_cmd = [sys.executable, str(test_tool), str(out), "--manifest", str(manifest), "--frames", str(args.frames)]
    if args.fresh:
        test_cmd.append("--fresh")
    if args.cold_save:
        test_cmd.append("--cold-save")
    elif args.save:
        test_cmd.append("--quick-save")
    if args.state:
        test_cmd += ["--state", args.state]
    if args.movie:
        test_cmd += ["--movie", args.movie]
    return subprocess.call(test_cmd)


if __name__ == "__main__":
    raise SystemExit(main())
