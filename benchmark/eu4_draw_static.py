#!/usr/bin/env python3
"""Version-pinned, read-only disassembly inventory of GOG EU IV draw callers."""

from __future__ import annotations

import bisect
import json
import re
import subprocess
from pathlib import Path

import eu4_benchmark as base


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "analysis/draw-callers.json"
HEADER = ROOT / "benchmark/eu4_draw_sites.h"
EXPECTED_SHA256 = "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"
IMAGE_BASE = 0x100000000
TARGETS = ("_GfxDrawIndexed", "_GfxDraw", "_GfxDrawIndexedInstanced",
           "_GfxDrawInstanced", "_GfxDraw2dLines")
SYMBOL = re.compile(r"^([0-9a-f]{16}) \(__TEXT,__text\) .*? (\S+)$")
CALL = re.compile(r"^\s*([0-9a-f]+):.*?\b(callq|jmpq)\s+.*?<(\w+)>\s*$")


def inventory() -> dict:
    if base.sha256(base.GOG_EXE) != EXPECTED_SHA256:
        raise base.BenchmarkError("Installed GOG EU IV is not the pinned executable")
    listed = subprocess.run(["nm", "-nm", str(base.GOG_EXE)], capture_output=True,
                            text=True, check=True).stdout
    symbols = []
    for line in listed.splitlines():
        match = SYMBOL.match(line)
        if match:
            symbols.append((int(match[1], 16), match[2]))
    addresses = [address for address, _ in symbols]
    process = subprocess.Popen(["xcrun", "llvm-objdump", "--disassemble",
                                "--symbolize-operands", "--no-show-raw-insn",
                                str(base.GOG_EXE)], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    assert process.stdout is not None
    sites = []
    for line in process.stdout:
        match = CALL.match(line)
        if not match or match[3] not in TARGETS:
            continue
        address = int(match[1], 16)
        position = bisect.bisect_right(addresses, address) - 1
        owner = symbols[position][1] if position >= 0 else "unknown"
        sites.append({"callsite_offset": address-IMAGE_BASE,
                      "return_offset": address-IMAGE_BASE+5,
                      "operation": match[3][1:], "instruction": match[2],
                      "owner_symbol": owner})
    error = process.stderr.read() if process.stderr else ""
    if process.wait() or not sites:
        raise base.BenchmarkError(f"Draw disassembly failed: {error[-1000:]}")
    names = [site["owner_symbol"] for site in sites]
    demangled = subprocess.run(["c++filt"], input="\n".join(names)+"\n",
                               capture_output=True, text=True, check=True).stdout.splitlines()
    for site, name in zip(sites, demangled):
        site["owner"] = name
        symbol = site["owner_symbol"]
        if "CPdxMeshObject" in symbol:
            category = "mesh_object"
        elif any(label in name for label in ("CBitmapFont::", "CEU3BitmapFont::",
                                              "SpriteType::", "C2dPieChart::")):
            category = "ui_or_text"
        elif any(label in name for label in ("CCountryNameCollection::", "CAnimatedMapText::")):
            category = "map_text"
        elif "CPdxMapTerrainLayer::" in name:
            category = "terrain"
        elif "CPdxMapBorderLayer::" in name:
            category = "borders"
        elif any(label in name for label in ("CPdxMapLakeLayer::",
                                              "CPdxMapInlandWaterLayer::")):
            category = "lakes_and_rivers"
        elif "CPdxMapTreeLayer::" in name:
            category = "trees"
        elif "CPdxParticleObject::" in name:
            category = "particles"
        elif any(label in name for label in ("CEU3GraphicalMap::RenderPostEffects",
                                              "CWrapWorldShadowMap::")):
            category = "postprocess"
        elif "CPdxMapSkyLayer::" in name:
            category = "sky"
        elif any(label in name for label in ("RenderGuiBounds", "RenderTimingStats",
                                              "CDebugLineHelper::")):
            category = "debug"
        elif "CFlagTexturemap::" in name:
            category = "texture_generation"
        elif any(label in name for label in ("CProgressbarSpriteType::",
                                              "CCorneredTileSpriteType::")):
            category = "ui_or_text"
        elif any(label in name for label in ("Map", "Terrain", "Province", "Border", "River")):
            category = "map_or_overlay"
        else:
            category = "unclassified"
        site["category"] = category
    return {"executable_sha256": EXPECTED_SHA256, "image_base": IMAGE_BASE,
            "direct_sites": sorted(sites, key=lambda site: site["callsite_offset"]),
            "limits": ["Direct call/jump inventory only; indirect calls and GL draws outside "
                       "these helpers require dynamic attribution.",
                       "Static categories are broad; counts are established by the passive trace."]}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "verify"))
    args = parser.parse_args()
    data = inventory()
    if args.command == "generate":
        OUTPUT.write_text(json.dumps(data, indent=2)+"\n")
        returns = ",".join(f"0x{site['return_offset']:x}u"
                          for site in data["direct_sites"])
        HEADER.write_text("// Generated by eu4_draw_static.py for the pinned GOG binary.\n"
                          f"#define EU4_DRAW_SITE_COUNT {len(data['direct_sites'])}u\n"
                          f"static const uint32_t eu4_draw_site_returns[]={{ {returns} }};\n")
    else:
        if data != json.loads(OUTPUT.read_text()):
            raise base.BenchmarkError("Draw call-site inventory differs from pinned output")
        if not HEADER.is_file():
            raise base.BenchmarkError("Draw call-site header is missing")
    print(f"{len(data['direct_sites'])} direct GfxDraw call sites; {args.command} passed")


if __name__ == "__main__":
    main()
