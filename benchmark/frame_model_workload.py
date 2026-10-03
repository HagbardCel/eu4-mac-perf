"""Structural GL overhead recipes from a complete passive frame, with no asset payloads."""
from __future__ import annotations

import collections
import hashlib
import json
import random
import shutil
import statistics
import struct
import subprocess
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
HELD_OUT_DIR = ROOT / "analysis/held-out"
HELD_OUT_FIXTURE = HELD_OUT_DIR / "frame-model-held-out-terrain-surrogate.recipe"
HELD_OUT_FIXTURE_META = HELD_OUT_DIR / "frame-model-held-out-terrain-surrogate.json"
HELD_OUT_FIXTURE_PATH = HELD_OUT_FIXTURE.relative_to(ROOT).as_posix()
TIER1_PAIR_COUNT = 7
TIER1_V4_PAIR_COUNT = 21
# Minimum structural draw records for terrain_odd selection (not the seven benchmark trial pairs).
HELD_OUT_MIN_DRAW_RECORDS = 7
HELD_OUT_SELECTION_RULE_ID = "terrain_odd_ordinal_within_frame_v1"
HELD_OUT_SOURCE_DIR = ROOT / "analysis/held-out-source"
HELD_OUT_SOURCE_PROJECTION = HELD_OUT_SOURCE_DIR / "terrain-frame-1-6270-projection.json"
FROZEN_HELD_OUT_RECIPE_SHA256 = "dbb0af0e7964777b7ab9f0fecde068692377719bde1670c2a082ecbb26281503"

# Recipe bytes already measured without a pre-committed fixture (exploratory archives).
EXPLORATORY_HELD_OUT_SHA256S = frozenset(
    {
        "284365e71435d8b6744440c5f5a028803527f5f1848f56365b85b5957c579188",
    },
)


def _git_tracked_at_head(path: Path) -> bool:
    """True when path exists as a blob in the current HEAD tree (committed, not index-only)."""
    relative = path.relative_to(ROOT).as_posix()
    try:
        subprocess.run(
            ["git", "cat-file", "-e", f"HEAD:{relative}"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        )
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def _reject_contaminated_fixture_sha(sha256: str) -> None:
    if sha256 in EXPLORATORY_HELD_OUT_SHA256S:
        raise base.BenchmarkError(
            "Held-out fixture was previously measured as exploratory evidence "
            f"(sha256 {sha256}); choose fresh recipe bytes",
        )


def held_out_fixture_provenance() -> dict:
    """Provenance stamped on recipes measured from the committed held-out fixture."""
    meta = json.loads(HELD_OUT_FIXTURE_META.read_text())
    expected = meta.get("sha256")
    if not expected or base.sha256(HELD_OUT_FIXTURE) != expected:
        raise base.BenchmarkError("Held-out fixture SHA256 does not match frozen metadata")
    _reject_contaminated_fixture_sha(expected)
    return {
        "admission_qualified": True,
        "fixture_committed": True,
        "fixture_path": HELD_OUT_FIXTURE_PATH,
        "fixture_sha256": expected,
    }


def held_out_fixture_ready() -> bool:
    """True when hash-frozen held-out recipe bytes are committed at HEAD (not gitignored locals)."""
    if not HELD_OUT_FIXTURE.is_file() or not HELD_OUT_FIXTURE_META.is_file():
        return False
    if not _git_tracked_at_head(HELD_OUT_FIXTURE) or not _git_tracked_at_head(HELD_OUT_FIXTURE_META):
        return False
    meta = json.loads(HELD_OUT_FIXTURE_META.read_text())
    expected = meta.get("sha256")
    if not expected or base.sha256(HELD_OUT_FIXTURE) != expected:
        return False
    if expected in EXPLORATORY_HELD_OUT_SHA256S:
        return False
    return True


def _row_category(row, sites):
    offset = row.get("return_offset", row.get("caller_offset", row.get("immediate_offset")))
    return sites.get(offset)


def _select_held_out_terrain_rows(terrain_rows: list[dict]) -> list[dict]:
    selected = [row for index, row in enumerate(terrain_rows) if index % 2 == 1]
    if len(selected) < HELD_OUT_MIN_DRAW_RECORDS:
        raise base.BenchmarkError(
            f"Held-out selection {HELD_OUT_SELECTION_RULE_ID} produced {len(selected)} draw records; "
            f"need >= {HELD_OUT_MIN_DRAW_RECORDS}",
        )
    if any(
        row["flags"] or row["api"] not in (1, 2, 3) or row["mode"] not in (4, 5) for row in selected
    ):
        raise base.BenchmarkError("Invalid held-out selection rows for structural recipe")
    return selected


def _finalize_held_out_fixture(
    selected: list[dict],
    key: tuple[int, int],
    *,
    source_sha256: str | None,
) -> tuple[bytes, dict]:
    payload = _build_recipe_payload(selected)
    sha256 = hashlib.sha256(payload).hexdigest()
    if sha256 in EXPLORATORY_HELD_OUT_SHA256S:
        raise base.BenchmarkError(
            "Held-out selection matches exploratory denylist SHA; choose a different selection rule",
        )
    meta = _recipe_dict(
        HELD_OUT_RECIPE_NAME,
        HELD_OUT_FIXTURE,
        payload,
        key,
        role="held_out",
        categories=HELD_OUT_CATEGORIES,
    )
    meta.pop("path", None)
    meta["held_out_selection_rule"] = HELD_OUT_SELECTION_RULE_ID
    meta["sha256"] = sha256
    meta["draws"] = len(selected)
    meta["indices"] = sum(row["count"] for row in selected)
    if source_sha256 is not None:
        meta["source_sha256"] = source_sha256
    return payload, meta


def build_held_out_fixture_material_from_projection(
    projection_path: Path = HELD_OUT_SOURCE_PROJECTION,
) -> tuple[bytes, dict]:
    """Rebuild held-out bytes from the committed terrain source projection (CI replay)."""
    if not projection_path.is_file():
        raise base.BenchmarkError(f"Held-out source projection missing: {projection_path}")
    projection = json.loads(projection_path.read_text())
    if projection.get("selection_rule") != HELD_OUT_SELECTION_RULE_ID:
        raise base.BenchmarkError("Held-out source projection selection_rule mismatch")
    key = tuple(projection["source_frame"])
    terrain_rows = projection["terrain_records"]
    selected = _select_held_out_terrain_rows(terrain_rows)
    return _finalize_held_out_fixture(
        selected,
        key,
        source_sha256=projection.get("source_trace_sha256"),
    )


def build_held_out_fixture_material(*, rows=None, key=None, sites=None) -> tuple[bytes, dict]:
    """Materialize held-out recipe bytes from the frozen selection rule (no harness timing)."""
    if rows is None and not SOURCE.is_file() and HELD_OUT_SOURCE_PROJECTION.is_file():
        return build_held_out_fixture_material_from_projection()
    if rows is None:
        if not SOURCE.is_file():
            raise base.BenchmarkError(
                "Passive trace and held-out source projection are unavailable for fixture materialization",
            )
        rows = list(trace.read_records(SOURCE, trace.read_header(SOURCE)["used"]))
    if key is None:
        key = _trace_frame_key(rows)
    if sites is None:
        sites = {row["return_offset"]: row["category"] for row in json.loads(INVENTORY.read_text())["direct_sites"]}
    terrain_rows = [
        row
        for row in rows
        if (row["window"], row["frame"]) == key and _row_category(row, sites) in HELD_OUT_CATEGORIES
    ]
    selected = _select_held_out_terrain_rows(terrain_rows)
    source_sha256 = base.sha256(SOURCE) if SOURCE.is_file() else None
    return _finalize_held_out_fixture(selected, key, source_sha256=source_sha256)


def write_held_out_fixture_files() -> dict:
    """Write analysis/held-out/* from passive trace using the frozen selection rule."""
    payload, meta = build_held_out_fixture_material()
    HELD_OUT_DIR.mkdir(parents=True, exist_ok=True)
    HELD_OUT_FIXTURE.write_bytes(payload)
    HELD_OUT_FIXTURE_META.write_text(json.dumps(meta, indent=2) + "\n")
    return meta


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
        if not held_out_fixture_ready():
            raise base.BenchmarkError(
                "include_held_out requires a committed hash-frozen fixture under analysis/held-out/",
            )
        result.append(held_out_recipe(directory, rows=rows, key=key, sites=sites))
    return result


def held_out_recipe(directory, *, rows=None, key=None, sites=None):
    """Load the hash-frozen held-out workload committed under analysis/held-out/."""
    if not held_out_fixture_ready():
        raise base.BenchmarkError(
            "Held-out recipe fixture is not hash-frozen in the repository "
            f"(commit {HELD_OUT_FIXTURE} and {HELD_OUT_FIXTURE_META} before measuring held-out performance)",
        )
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
    recipe.update(held_out_fixture_provenance())
    return recipe


def paired_summary(
    pairs,
    limit,
    frames=1,
    *,
    bootstrap_seed=1729,
    bootstrap_samples=2000,
    ci_low_index=50,
    ci_high_index=1950,
):
    ratios = [pair["instrumented"] / pair["reference"] - 1 for pair in pairs]
    rng = random.Random(bootstrap_seed)
    bootstrap = sorted(
        statistics.median(rng.choices(ratios, k=len(ratios))) for _ in range(bootstrap_samples)
    )
    interval = [bootstrap[ci_low_index], bootstrap[ci_high_index]]
    absolute = [(pair["instrumented"] - pair["reference"]) / (1000 * frames) for pair in pairs]
    absolute_bootstrap = sorted(
        statistics.median(rng.choices(absolute, k=len(absolute))) for _ in range(bootstrap_samples)
    )
    median = statistics.median(ratios)
    return {
        "median_fraction": median,
        "confidence_interval_95": interval,
        "pairs": pairs,
        "overhead_us_per_frame": statistics.median(absolute),
        "overhead_us_per_frame_ci95": [absolute_bootstrap[ci_low_index], absolute_bootstrap[ci_high_index]],
        "bootstrap_seed": bootstrap_seed,
        "bootstrap_samples": bootstrap_samples,
        "bootstrap_ci_indices": [ci_low_index, ci_high_index],
        "absolute_metric_policy": "Tier-1 absolute cap enforced in frame_model_tier1_policy",
        "limit": limit,
        "status": "passed"
        if max(abs(median), abs(interval[0]), abs(interval[1])) <= limit
        else "failed",
    }
