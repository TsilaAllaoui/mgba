#!/usr/bin/env python3
"""Create a strict mGBA-GBABR sidecar manifest for a patched GBA ROM."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from gbabr_common import (
    CAPACITY,
    SCHEMA,
    TARGET_KEY_TO_PROFILE,
    normalize_profile,
    parse_patch_report,
    parse_range_spec,
    sha256_file,
    write_manifest,
)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Create <ROM>.gbabr.json from a GBASaveHandler/libgbasave patch report."
    )
    ap.add_argument("rom", help="patched .gba ROM")
    ap.add_argument("--report", help="GBASaveHandler patch report")
    ap.add_argument("--cart", help="override cart profile: m6m,m28,m36,m6mgd137,mx26,s29")
    ap.add_argument("--rom-offset", default="0", help="physical ROM offset, especially for Mini128 (default 0)")
    ap.add_argument("--allow", action="append", default=[], metavar="START:LENGTH", help="extra ROM-local mutable range")
    ap.add_argument("--allow-physical", action="append", default=[], metavar="START:LENGTH", help="extra physical mutable range")
    ap.add_argument("--non-strict", action="store_true", help="allow writes outside declared ranges (not recommended)")
    ap.add_argument("--native-save", action="store_true", help="allow native SaveRAM accesses")
    ap.add_argument("-o", "--output", help="manifest output path")
    args = ap.parse_args()

    rom = Path(args.rom).resolve()
    if not rom.is_file():
        ap.error(f"ROM not found: {rom}")
    rom_offset = int(args.rom_offset, 0)
    report_info = {"target_key": None, "output_sha256": None, "output_size": None, "ranges": []}
    report_path = None
    if args.report:
        report_path = Path(args.report).resolve()
        if not report_path.is_file():
            ap.error(f"report not found: {report_path}")
        report_info = parse_patch_report(report_path.read_text(encoding="utf-8", errors="replace"))

    profile = None
    if args.cart:
        profile = normalize_profile(args.cart)
    elif report_info["target_key"]:
        target = report_info["target_key"]
        if target in TARGET_KEY_TO_PROFILE:
            profile = TARGET_KEY_TO_PROFILE[target]
    if not profile:
        ap.error("could not infer cart profile; pass --cart")

    digest = sha256_file(rom)
    report_sha = report_info.get("output_sha256")
    if report_sha and report_sha != digest:
        ap.error(f"report Output SHA256 does not match ROM\nreport {report_sha}\nROM    {digest}")

    capacity = CAPACITY[profile]
    if rom_offset < 0 or rom_offset + rom.stat().st_size > capacity:
        ap.error(
            f"ROM at offset 0x{rom_offset:X} does not fit {profile} capacity 0x{capacity:X}"
        )

    mutable = []
    for r in report_info["ranges"]:
        mutable.append(
            {
                "offset": rom_offset + int(r["offset"]),
                "bytes": int(r["bytes"]),
                "source": f"patch report {r['source']}",
            }
        )
    for spec in args.allow:
        off, size = parse_range_spec(spec)
        mutable.append({"offset": rom_offset + off, "bytes": size, "source": "manual local"})
    for spec in args.allow_physical:
        off, size = parse_range_spec(spec)
        mutable.append({"offset": off, "bytes": size, "source": "manual physical"})

    # Sort + exact-deduplicate, leaving overlaps visible rather than silently broadening them.
    seen = set()
    dedup = []
    for r in sorted(mutable, key=lambda x: (x["offset"], x["bytes"], x["source"])):
        key = (r["offset"], r["bytes"])
        if key in seen:
            continue
        seen.add(key)
        if r["offset"] < 0 or r["offset"] + r["bytes"] > capacity:
            ap.error(f"mutable range outside cart capacity: 0x{r['offset']:X}:0x{r['bytes']:X}")
        dedup.append(r)

    if not dedup and not args.non_strict:
        print("WARNING: strict manifest has no mutable NOR ranges; all NOR program/erase attempts will fail closed.", file=sys.stderr)

    output = Path(args.output).resolve() if args.output else rom.with_suffix(".gbabr.json")
    data = {
        "schema": SCHEMA,
        "rom": {
            "file": rom.name,
            "sha256": digest,
            "bytes": rom.stat().st_size,
        },
        "cart": {
            "profile": profile,
            "target_key": report_info.get("target_key") or profile,
            "capacity": capacity,
            "rom_offset": rom_offset,
            "strict": not args.non_strict,
            "native_save_allowed": bool(args.native_save or profile == "s29"),
        },
        "mutable_ranges": dedup,
        "source": {
            "patch_report": str(report_path) if report_path else None,
        },
    }
    write_manifest(output, data)
    print(f"Manifest: {output}")
    print(f"ROM SHA256: {digest}")
    print(f"Cart: {profile} | ROM offset 0x{rom_offset:X} | mutable ranges {len(dedup)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
