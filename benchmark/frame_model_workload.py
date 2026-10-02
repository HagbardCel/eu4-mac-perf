"""Structural GL overhead recipes from a complete passive frame, with no asset payloads."""
import collections
import hashlib
import json
import random
import shutil
import statistics
import struct
from pathlib import Path

import eu4_draw_trace as trace
import eu4_benchmark as base

SOURCE = trace.ROOT / "results/20260928T113359Z-draw-trace/trace.bin"
INVENTORY = trace.ROOT / "analysis/draw-callers.json"
RECORD = struct.Struct("<8I")
ROOT = trace.ROOT

TRAINING_GROUPS = {
    "mesh": {"mesh_object"},
    "borders": {"borders"},
    "text_ui": {"map_text", "ui_or_text"},
}
HELD_OUT_RECIPE_NAME = "terrain_surrogate"
HELD_OUT_CATEGORIES = frozenset({"terrain"})
HELD_OUT_FIXTURE = ROOT / "analysis/fixtures/frame-model-held-out-terrain-surrogate.recipe"
HELD_OUT_FIXTURE_META = ROOT / "analysis/fixtures/frame-model-held-out-terrain-surrogate.json"


def _trace_frame_key(rows):
    frames = collections.Counter((r["window"], r["frame"]) for r in rows)
    key = next((key for key, count in frames.items() if count == 5863), None)
    if key is None:
        raise base.BenchmarkError("No complete 5863-draw passive frame for recipe validation")
    return key


def _build_recipe_payload(selected):
    payload = bytearray()
    previous = None
    for record in selected:
        change = lambda field, row=record, prev=previous: int(prev is None or row[field] != prev[field])
        payload.extend(
            RECORD.pack(
                record["api"],
                record["mode"],
                record["count"],
                change("uniform_sig"),
                change("texture_sig"),
                change("render_sig"),
                change("vertex_sig"),
                change("program"),
            ),
        )
        previous = record
    return payload


def _recipe_dict(name, path, payload, key, *, role: str, categories: set[str]) -> dict:
    return {
        "name": name,
        "role": role,
        "path": str(path),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "draws": len(payload) // RECORD.size if payload else 0,
        "indices": 0,
        "source_frame": list(key),
        "source_sha256": base.sha256(SOURCE) if SOURCE.is_file() else None,
        "categories": sorted(categories),
        "policy": (
            "Observed draw counts/topology/API and adjacent state-change frequencies; "
            "generated valid resources and shaders, no sleeps or added busywork."
        ),
        "limits": (
            "GL structural surrogate; original resource payloads, shaders, and unobserved "
            "between-draw commands are not reconstructed. Live perturbation remains mandatory."
        ),
    }


def _recipe_from_categories(name, categories, rows, key, sites, directory, *, role: str):
    selected = [
        row
        for row in rows
        if (row["window"], row["frame"]) == key
        and sites.get(row["caller_offset"], sites.get(row["immediate_offset"])) in categories
    ]
    if not selected or any(
        row["flags"] or row["api"] not in (1, 2, 3) or row["mode"] not in (4, 5) for row in selected
    ):
        raise base.BenchmarkError(f"Invalid or unsupported trace recipe: {name}")
    payload = _build_recipe_payload(selected)
    path = directory / f"{name}.recipe"
    path.write_bytes(payload)
    recipe = _recipe_dict(name, path, payload, key, role=role, categories=set(categories))
    recipe["indices"] = sum(row["count"] for row in selected)
    recipe["draws"] = len(selected)
    return recipe


def recipes(directory, *, include_held_out: bool = False):
    if not SOURCE.is_file():
        raise base.BenchmarkError("Passive trace is unavailable for representative recipes")
    rows = list(trace.read_records(SOURCE, trace.read_header(SOURCE)["used"]))
    key = _trace_frame_key(rows)
    sites = {row["return_offset"]: row["category"] for row in json.loads(INVENTORY.read_text())["direct_sites"]}
    result = [
        _recipe_from_categories(name, categories, rows, key, sites, directory, role="training")
        for name, categories in TRAINING_GROUPS.items()
    ]
    if include_held_out:
        result.append(held_out_recipe(directory, rows=rows, key=key, sites=sites))
    return result


def held_out_recipe(directory, *, rows=None, key=None, sites=None):
    """Hash-frozen held-out workload (fixture preferred; else built from passive trace)."""
    if HELD_OUT_FIXTURE.is_file() and HELD_OUT_FIXTURE_META.is_file():
        meta = json.loads(HELD_OUT_FIXTURE_META.read_text())
        expected = meta.get("sha256")
        if expected and base.sha256(HELD_OUT_FIXTURE) != expected:
            raise base.BenchmarkError("Held-out fixture SHA256 does not match frozen metadata")
        path = directory / HELD_OUT_FIXTURE.name
        shutil.copy2(HELD_OUT_FIXTURE, path)
        recipe = dict(meta)
        recipe["path"] = str(path)
        recipe.setdefault("role", "held_out")
        recipe.setdefault("name", HELD_OUT_RECIPE_NAME)
        return recipe
    if rows is None or key is None or sites is None:
        if not SOURCE.is_file():
            raise base.BenchmarkError("Held-out recipe requires passive trace or committed fixture")
        rows = list(trace.read_records(SOURCE, trace.read_header(SOURCE)["used"]))
        key = _trace_frame_key(rows)
        sites = {row["return_offset"]: row["category"] for row in json.loads(INVENTORY.read_text())["direct_sites"]}
    return _recipe_from_categories(
        HELD_OUT_RECIPE_NAME,
        HELD_OUT_CATEGORIES,
        rows,
        key,
        sites,
        directory,
        role="held_out",
    )


def paired_summary(pairs, limit, frames=1):
    ratios = [pair["instrumented"] / pair["reference"] - 1 for pair in pairs]
    rng = random.Random(1729)
    bootstrap = sorted(statistics.median(rng.choices(ratios, k=len(ratios))) for _ in range(2000))
    interval = [bootstrap[50], bootstrap[1950]]
    absolute = [(pair["instrumented"] - pair["reference"]) / (1000 * frames) for pair in pairs]
    absolute_bootstrap = sorted(statistics.median(rng.choices(absolute, k=len(absolute))) for _ in range(2000))
    median = statistics.median(ratios)
    return {
        "median_fraction": median,
        "confidence_interval_95": interval,
        "pairs": pairs,
        "overhead_us_per_frame": statistics.median(absolute),
        "overhead_us_per_frame_ci95": [absolute_bootstrap[50], absolute_bootstrap[1950]],
        "absolute_metric_policy": "Tier-1 absolute cap enforced in frame_model_tier1_policy",
        "limit": limit,
        "status": "passed"
        if max(abs(median), abs(interval[0]), abs(interval[1])) <= limit
        else "failed",
    }
