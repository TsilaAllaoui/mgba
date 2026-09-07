#!/usr/bin/env python3
"""Shared helpers for the mGBA-GBABR virtual-cartridge test tools."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

SCHEMA = 1
PROFILE_ALIASES = {
    "m6": "m6m",
    "m6m": "m6m",
    "m28": "m28",
    "m28w640fs": "m28",
    "m28w640fs-t70za6": "m28",
    "m36": "m36",
    "m36-e8-128k": "m36",
    "m6mgd137": "m6mgd137",
    "d137": "m6mgd137",
    "mx26": "mx26",
    "mx26l6420": "mx26",
    "mx26l6420mc-90": "mx26",
    "s29": "s29",
    "mini128": "s29",
    "s29gl01gs": "s29",
}
CAPACITY = {
    "m6m": 8 * 1024 * 1024,
    "m28": 8 * 1024 * 1024,
    "m36": 16 * 1024 * 1024,
    "m6mgd137": 16 * 1024 * 1024,
    "mx26": 8 * 1024 * 1024,
    "s29": 128 * 1024 * 1024,
}
TARGET_KEY_TO_PROFILE = {
    "m6m": "m6m",
    "m28w640fs-t70za6": "m28",
    "m36": "m36",
    "m6mgd137": "m6mgd137",
    "mx26l6420mc-90": "mx26",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_profile(value: str) -> str:
    key = value.strip().lower()
    if key not in PROFILE_ALIASES:
        raise ValueError(f"unsupported GBABR virtual cart profile: {value}")
    return PROFILE_ALIASES[key]


def parse_int(text: str) -> int:
    return int(text.strip(), 0)


def parse_range_spec(spec: str) -> tuple[int, int]:
    """Parse START:LENGTH or START..END, inclusive END."""
    text = spec.strip()
    if ".." in text:
        a, b = text.split("..", 1)
        start, end = parse_int(a), parse_int(b)
        if end < start:
            raise ValueError(f"invalid range: {spec}")
        return start, end - start + 1
    if ":" in text:
        a, b = text.split(":", 1)
        start, length = parse_int(a), parse_int(b)
        if length <= 0:
            raise ValueError(f"invalid range length: {spec}")
        return start, length
    raise ValueError(f"range must be START:LENGTH or START..END: {spec}")


def parse_patch_report(text: str) -> dict[str, Any]:
    target_key = None
    m = re.search(r"^TARGET CART/NOR PROFILE: .*?\(([^()]+)\)\s*$", text, re.M)
    if m:
        target_key = m.group(1).strip().lower()

    output_sha = None
    m = re.search(r"^Output SHA256:\s*([0-9A-Fa-f]{64})\s*$", text, re.M)
    if m:
        output_sha = m.group(1).lower()

    ranges: list[dict[str, Any]] = []
    rx = re.compile(
        r"^\s*(block\s+\d+|journal\s+span\s+\d+)\s*->\s*"
        r"0x([0-9A-Fa-f]+)\.\.0x([0-9A-Fa-f]+)\b.*$",
        re.M,
    )
    for m in rx.finditer(text):
        start = int(m.group(2), 16)
        end = int(m.group(3), 16)
        if end >= start:
            ranges.append({"offset": start, "bytes": end - start + 1, "source": m.group(1)})

    output_size = None
    m = re.search(r"^\s*Output size:\s*(\d+)\s+bytes", text, re.M)
    if m:
        output_size = int(m.group(1))

    return {
        "target_key": target_key,
        "output_sha256": output_sha,
        "output_size": output_size,
        "ranges": ranges,
    }


def manifest_sidecar_candidates(rom: Path) -> list[Path]:
    return [Path(str(rom) + ".gbabr.json"), rom.with_suffix(".gbabr.json")]


def find_manifest(rom: Path, explicit: str | None = None) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            raise FileNotFoundError(f"manifest not found: {p}")
        return p
    for p in manifest_sidecar_candidates(rom):
        if p.is_file():
            return p
    raise FileNotFoundError(
        "no GBABR manifest found; run gbabr-manifest first or pass --manifest FILE"
    )


def load_manifest(path: Path, rom: Path | None = None) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ValueError(f"unsupported manifest schema: {data.get('schema')}")
    cart = data.get("cart", {})
    cart["profile"] = normalize_profile(str(cart.get("profile", "")))
    if "rom_offset" not in cart:
        cart["rom_offset"] = 0
    data["cart"] = cart
    if rom:
        expected = str(data.get("rom", {}).get("sha256", "")).lower()
        actual = sha256_file(rom)
        if expected and expected != actual:
            raise ValueError(
                f"ROM SHA256 does not match manifest\nexpected {expected}\nactual   {actual}"
            )
    return data


def default_workdir(rom: Path, manifest: dict[str, Any]) -> Path:
    digest = str(manifest.get("rom", {}).get("sha256") or sha256_file(rom))[:12]
    return rom.parent / ".gbabr-tests" / f"{rom.stem}-{digest}"


def environment_for_manifest(
    manifest: dict[str, Any], workdir: Path, *, events_path: Path | None = None
) -> dict[str, str]:
    cart = manifest["cart"]
    profile = normalize_profile(cart["profile"])
    ranges = manifest.get("mutable_ranges", [])
    allow = ",".join(
        f"0x{int(r['offset']):X}:0x{int(r['bytes']):X}" for r in ranges
    )
    env = os.environ.copy()
    env["MGBA_GBABR_PROFILE"] = profile
    env["MGBA_GBABR_STRICT"] = "1" if cart.get("strict", True) else "0"
    env["MGBA_GBABR_ALLOW"] = allow
    env["MGBA_GBABR_ROM_OFFSET"] = hex(int(cart.get("rom_offset", 0)))
    env["MGBA_GBABR_NOR"] = str(workdir / "cart.bin")
    if profile == "s29":
        env["MGBA_GBABR_FRAM"] = str(workdir / "fram.bin")
    else:
        env.pop("MGBA_GBABR_FRAM", None)
    if events_path is not None:
        env["MGBA_GBABR_EVENTS"] = str(events_path)
    env["MGBA_GBABR_NATIVE_SAVE_ALLOWED"] = (
        "1" if cart.get("native_save_allowed", profile == "s29") else "0"
    )
    return env


def find_executable(name: str, script_file: Path, env_name: str | None = None) -> Path | None:
    if env_name and os.environ.get(env_name):
        p = Path(os.environ[env_name])
        if p.is_file():
            return p
    here = script_file.resolve().parent
    candidates = [
        here / name,
        here.parent / "bin" / name,
        here.parent.parent / "bin" / name,
        here.parent.parent / "build-gbabr-static" / name,
        here.parent.parent / "build-gbabr" / name,
    ]
    for p in candidates:
        if p.is_file() and os.access(p, os.X_OK):
            return p
    found = shutil.which(name)
    return Path(found) if found else None


def write_manifest(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")
