"""Reviewed driver for the original-OpenGL selected-draw control.

The qualification gate executes this file.  It consumes only the restricted,
content-addressed replay-input package produced by
:mod:`automation.gfx_gl_replay_input`, translates it into the normalised control
file read by the pinned native executor, runs exactly that executor, and signs
the execution receipt with the renderer's Ed25519 key.

It never receives or reads the frame directory, the post-draw reference image,
the live image path or a Metal artifact.  The executable and every component it
reports are hashed from the bytes actually used, so the registered authority can
bind the exact execution path.

The signed payload is canonical JSON (sorted keys, compact separators, UTF-8,
no NaN) of the receipt object with the ``signature`` member removed.  The
signature object names that canonicalisation so an independent verifier can
reproduce it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from automation import gfx_ed25519
from automation import gfx_gl_replay_input as replay_input

RECEIPT_SCHEMA = "gfx-gl-control-receipt/v2"
EXECUTION_SCHEMA = "gfx-gl-control-execution/v1"
RENDERER_NAME = "m7-original-gl-control"
CANONICALISATION = "json-sorted-compact-utf8-excluding-signature"

_INTERNAL_FORMATS = {
    "RGBA8": 0x8058,
    "RGB8": 0x8051,
    "RGBA": 0x1908,
    "RGB": 0x1907,
    "BGRA": 0x80E1,
    "SRGB8_ALPHA8": 0x8C43,
    "RGBA16": 0x805B,
    "RGBA16F": 0x881A,
    "RGBA32F": 0x8814,
    "RGB16F": 0x881B,
    "RGB32F": 0x8815,
    "DXT1": 0x83F0,
    "DXT1_ALPHA": 0x83F1,
    "DXT3": 0x83F2,
    "DXT5": 0x83F3,
}
_TARGETS = {"2D": 0x0DE1}
_POLYGON_MODES = {0x1B00, 0x1B01, 0x1B02}  # POINT, LINE, FILL


class RendererError(RuntimeError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _enum(value, default=0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(value, 0)
        except ValueError:
            return default
    return default


def _signing_seed() -> bytes:
    text = os.environ.get("GL_CONTROL_SIGNING_KEY", "").strip()
    if not text:
        path = os.environ.get("GL_CONTROL_SIGNING_KEY_FILE", "").strip()
        if path:
            try:
                text = Path(path).read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise RendererError(f"signing key file is unreadable: {exc}") from exc
    try:
        seed = bytes.fromhex(text)
    except ValueError as exc:
        raise RendererError("signing key is not hexadecimal") from exc
    if len(seed) != 32:
        raise RendererError("signing key must be a 32-byte Ed25519 seed")
    return seed


def _component(role: str, path: Path) -> dict:
    path = Path(path).resolve()
    return {"role": role, "path": str(path), "sha256": _sha256_file(path)}


def _native_executable() -> Path:
    explicit = os.environ.get("GL_CONTROL_EXECUTABLE", "").strip()
    if explicit:
        path = Path(explicit).expanduser().resolve()
    else:
        path = Path(__file__).with_name("eu4_gfx_draw_control")
    if not path.is_file():
        raise RendererError(f"native original-GL executor is absent: {path}")
    return path


def _package_path(manifest: dict, package_dir: Path, value) -> Path:
    """Resolve a manifest-referenced file, but only inside the declared set."""
    declared = {record.get("path") for record in manifest.get("files") or []}
    text = str(value or "")
    relative = Path(text)
    if not text or relative.is_absolute() or text not in declared:
        raise RendererError(
            f"manifest path {text!r} is not a declared package file")
    path = (package_dir / relative).resolve()
    try:
        path.relative_to(package_dir.resolve())
    except ValueError as exc:
        raise RendererError(
            f"manifest path {text!r} escapes the package") from exc
    if not path.is_file():
        raise RendererError(f"manifest path {text!r} is absent")
    return path


def _ctl_lines(manifest: dict, package_dir: Path) -> list[str]:
    identity = manifest["identity"]
    drawable = manifest["drawable"]
    prestate = manifest["prestate"]
    draw = manifest["draw"]
    state = manifest["state"]
    lines = [
        f"width {identity['width']}",
        f"height {identity['height']}",
    ]
    for field in ("samples", "sample_buffers", "depth_bits", "stencil_bits",
                  "red_bits", "green_bits", "blue_bits", "alpha_bits"):
        lines.append(f"drawable {field} {int(drawable[field])}")
    srgb = state.get("framebuffer_srgb")
    srgb_known = isinstance(srgb, bool)
    lines.append(f"drawable srgb {1 if srgb else 0}")
    lines.append(f"drawable srgb_known {1 if srgb_known else 0}")
    lines.append(
        f"drawable double_buffer {1 if drawable.get('double_buffer') else 0}")
    lines.append(f"drawable width {int(drawable['width'])}")
    lines.append(f"drawable height {int(drawable['height'])}")

    lines.append(f"state srgb {1 if srgb else 0}")
    lines.append(f"state srgb_known {1 if srgb_known else 0}")
    lines.append(f"state dither {1 if state.get('dither') else 0}")
    lines.append(f"state multisample {1 if state.get('multisample') else 0}")
    blend = state.get("blend") or {}
    lines.append(f"state blend {1 if blend.get('enabled') else 0}")
    for field, key in (("blend_src_rgb", "src_rgb"),
                       ("blend_dst_rgb", "dst_rgb"),
                       ("blend_src_alpha", "src_alpha"),
                       ("blend_dst_alpha", "dst_alpha"),
                       ("blend_eq_rgb", "equation_rgb"),
                       ("blend_eq_alpha", "equation_alpha")):
        lines.append(f"state {field} 0x{_enum(blend.get(key)):x}")
    mask = state.get("color_mask") or [1, 1, 1, 1]
    lines.append("state color_mask " + " ".join(str(int(v)) for v in mask))
    depth = state.get("depth") or {}
    lines.append(f"state depth_test {1 if depth.get('test') else 0}")
    lines.append(f"state depth_func 0x{_enum(depth.get('func')):x}")
    lines.append(f"state depth_write {1 if depth.get('write') else 0}")
    depth_range = depth.get("range") or [0.0, 1.0]
    lines.append(f"state depth_near {float(depth_range[0])!r}")
    lines.append(f"state depth_far {float(depth_range[1])!r}")
    stencil = state.get("stencil") or {}
    lines.append(f"state stencil_test {1 if stencil.get('test') else 0}")
    lines.append(f"state stencil_func 0x{_enum(stencil.get('func')):x}")
    lines.append(f"state stencil_ref {int(stencil.get('ref') or 0)}")
    lines.append(
        f"state stencil_value_mask {int(stencil.get('value_mask', -1))}")
    for field, key in (("stencil_fail", "fail"), ("stencil_zfail", "zfail"),
                       ("stencil_zpass", "zpass")):
        lines.append(f"state {field} 0x{_enum(stencil.get(key)):x}")
    lines.append(
        f"state stencil_write_mask {int(stencil.get('write_mask', -1))}")
    cull = state.get("cull") or {}
    lines.append(f"state cull {1 if cull.get('enabled') else 0}")
    lines.append(f"state cull_mode 0x{_enum(cull.get('mode')):x}")
    lines.append(f"state front_face 0x{_enum(cull.get('front')):x}")
    polygon = state.get("polygon") or {}
    modes = polygon.get("mode")
    if not (isinstance(modes, (list, tuple)) and len(modes) == 2):
        raise RendererError(
            "the captured polygon mode is not a front/back pair")
    front_mode = _enum(modes[0])
    back_mode = _enum(modes[1])
    for label, mode in (("front", front_mode), ("back", back_mode)):
        if mode not in _POLYGON_MODES:
            raise RendererError(
                f"unsupported captured {label} polygon mode 0x{mode:x}")
    lines.append(f"state polygon_mode_front 0x{front_mode:x}")
    lines.append(f"state polygon_mode_back 0x{back_mode:x}")
    lines.append(
        f"state polygon_offset_fill {1 if polygon.get('offset_fill') else 0}")
    lines.append(
        f"state polygon_offset_factor {float(polygon.get('offset_factor') or 0)!r}")
    lines.append(
        f"state polygon_offset_units {float(polygon.get('offset_units') or 0)!r}")
    alpha_test = state.get("alpha_test") or {}
    lines.append(f"state alpha_test {1 if alpha_test.get('enabled') else 0}")
    lines.append(f"state alpha_func 0x{_enum(alpha_test.get('func')):x}")
    lines.append(f"state alpha_ref {float(alpha_test.get('ref') or 0)!r}")
    scissor = state.get("scissor") or {}
    lines.append(f"state scissor {1 if scissor.get('enabled') else 0}")
    box = scissor.get("box") or [0, 0, 0, 0]
    lines.append("state scissor_box " + " ".join(str(int(v)) for v in box))
    viewport = state.get("viewport") or [0, 0, identity["width"],
                                         identity["height"]]
    lines.append("state viewport " + " ".join(str(int(v)) for v in viewport))
    logic = state.get("logic_op") or {}
    lines.append(f"state logic_op_enabled {1 if logic.get('enabled') else 0}")
    lines.append(f"state logic_op 0x{_enum(logic.get('mode')):x}")

    for label in ("color", "depth", "stencil"):
        record = prestate.get(label) or {}
        source = record.get("source")
        if source == "checkpoint":
            lines.append(
                f"seed {label} file "
                f"{_package_path(manifest, package_dir, record['path'])}")
        elif source == "clear_history":
            value = record.get("value")
            if label == "color":
                raise RendererError(
                    "a clear-history colour seed is not supported; the "
                    "restricted package must carry the captured colour image")
            lines.append(f"seed {label} value {value}")
        elif source == "not_required":
            lines.append(f"seed {label} value {1.0 if label == 'depth' else 0}")
        else:
            raise RendererError(f"unsupported {label} prestate source {source!r}")

    kind = "indexed" if draw.get("indexed") else "arrays"
    index_path = ""
    if manifest.get("indices"):
        index_path = str(_package_path(
            manifest, package_dir, manifest["indices"]["path"]))
    first = int(draw.get("first") or 0)
    first_vertices = {
        int(attribute.get("first_vertex") or 0)
        for attribute in manifest["attributes"]}
    if len(first_vertices) > 1:
        raise RendererError(
            "the captured attributes disagree on their first vertex; a "
            "single vertex origin cannot be chosen")
    if not draw.get("indexed"):
        origin = next(iter(first_vertices)) if first_vertices else 0
        first -= origin
        if first < 0:
            raise RendererError("the captured draw starts before its range")
    lines.append(
        f"draw {kind} 0x{int(draw['mode']):x} {int(draw['count'])} "
        f"{first} "
        f"{int((manifest.get('indices') or {}).get('first_index') or 0)} "
        f"{int(draw.get('base_vertex') or 0)} "
        f"{int(draw.get('instance_count') or 1)} {index_path}")
    program = manifest["program"]
    for shader in program["shaders"]:
        stage = "vertex" if shader["stage"] == "vertex" else "pixel"
        lines.append(
            f"shader {stage} "
            f"{_package_path(manifest, package_dir, shader['path'])}")
    for attribute in manifest["attributes"]:
        relative = int(attribute["offset"]) - int(attribute.get("range_begin") or 0)
        if relative < 0:
            if draw.get("indexed"):
                raise RendererError(
                    "the captured attribute range begins after its offset; "
                    "indexed vertex rebasing is not supported")
            # The uploaded range starts at the first captured vertex, so the
            # pointer offset within it is zero and the draw's first vertex is
            # rebased below.
            relative = 0
        if relative > int(attribute["data_bytes"]):
            raise RendererError(
                f"attribute {attribute.get('name')} offset is outside its "
                "captured range")
        lines.append(
            "attrib %d %s %d %d 0x%x %d %d %d %d %d %s"
            % (int(attribute["index"]), attribute.get("name") or "",
               1 if attribute.get("enabled") else 0,
               int(attribute["components"]), _enum(attribute["gl_type"]),
               1 if attribute.get("normalized") else 0,
               1 if attribute.get("integer") else 0,
               int(attribute["stride"]), relative,
               int(attribute.get("divisor") or 0),
               _package_path(manifest, package_dir,
                             attribute["data_path"])))
    if draw.get("indexed") and first_vertices not in ({0}, set()):
        raise RendererError(
            "indexed draws whose captured vertices do not start at zero are "
            "not supported")
    samplers: list[tuple[str, int]] = []
    for uniform in program["uniforms"]:
        if uniform.get("type") == "sampler":
            name = uniform.get("name")
            unit = uniform.get("unit")
            if not isinstance(name, str) or not name:
                raise RendererError(
                    "a captured sampler uniform has no name")
            if isinstance(unit, bool) or not isinstance(unit, int) or unit < 0:
                raise RendererError(
                    f"sampler {name} has no captured texture unit")
            if "[" in name:
                raise RendererError(
                    f"sampler {name} is an array; its element bindings were "
                    "not captured")
            samplers.append((name, unit))
            continue
        lines.append(
            "uniform %s %s %d %d %s"
            % (uniform["name"], uniform["type"],
               int(uniform.get("components") or 4),
               int(uniform.get("elements") or 1),
               _package_path(manifest, package_dir, uniform["path"])))
    texture_units = {int(texture["unit"]) for texture in manifest["textures"]}
    for name, unit in samplers:
        if unit not in texture_units:
            raise RendererError(
                f"sampler {name} names texture unit {unit}, which has no "
                "captured texture")
        # One line per active sampler uniform: texture uploads are
        # deduplicated by unit, but uniform bindings are not.
        lines.append(f"sampler {name} {unit}")
    for texture in manifest["textures"]:
        unit = int(texture["unit"])
        target = _TARGETS.get(str(texture.get("target")))
        if target is None:
            raise RendererError(
                f"unsupported texture target {texture.get('target')!r}")
        sampler = texture.get("sampler") or {}
        lines.append(
            "texture %d 0x%x 0x%x 0x%x 0x%x 0x%x 0x%x %r %r %r %d %d 0x%x "
            "0x%x %d %s"
            % (unit, target, _enum(sampler.get("min")),
               _enum(sampler.get("mag")), _enum(sampler.get("wrap_s")),
               _enum(sampler.get("wrap_t")), _enum(sampler.get("wrap_r")),
               float(sampler.get("min_lod") or 0),
               float(sampler.get("max_lod") or 0),
               float(sampler.get("lod_bias") or 0),
               int(sampler.get("base_level") or 0),
               int(sampler.get("max_level") or 0),
               _enum(sampler.get("compare_mode")),
               _enum(sampler.get("compare_func")),
               int(sampler.get("anisotropy") or 1),
               texture.get("sampler_name") or ""))
        for level in texture.get("levels") or []:
            name = str(level.get("internal_format") or "")
            internal = _INTERNAL_FORMATS.get(name)
            if internal is None:
                raise RendererError(
                    f"unsupported captured texture format {name!r}")
            lines.append(
                "texlevel %d %d %d %d 0x%x %d %d %d %s"
                % (unit, int(level["level"]), int(level["width"]),
                   int(level["height"]), internal,
                   1 if level.get("compressed") else 0,
                   int(level["bytes"]), int(level.get("row_stride") or 0),
                   _package_path(manifest, package_dir, level["path"])))
    return lines


def _manifest_identity(request: dict) -> dict:
    """Use the verified manifest identity, never the mutable request alone."""
    expected = request.get("expected") or {}
    package = replay_input.verify_package(request["input_package"])
    identity = package["manifest_document"].get("identity") or {}
    mapping = {
        "session_id": identity.get("session_id"),
        "capture_frame_id": identity.get("capture_frame_id"),
        "present_index": identity.get("present_index"),
        "event_stream_sha256": identity.get("event_stream_sha256"),
        "checkpoint_event": identity.get("checkpoint_event"),
        "draw_event": identity.get("draw_event"),
        "resource": identity.get("resource"),
    }
    expected = dict(expected)
    expected["resource"] = expected.get("checkpoint_resource")
    for field, value in mapping.items():
        if expected.get(field) != value:
            raise RendererError(
                f"request {field} does not match the verified package")
    return mapping


def _write_receipt(*, out: Path, request: dict, execution: dict,
                   artifact: Path | None, renderer_path: Path,
                   components: list[dict], seed: bytes) -> dict:
    identity = execution.get("identity") or _manifest_identity(request)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "nonce": request["nonce"],
        "renderer": {
            "name": os.environ.get("GL_CONTROL_RENDERER_NAME", RENDERER_NAME),
            "sha256": _sha256_file(renderer_path),
            "components": components,
        },
        "api": "opengl",
        "execution": execution.get("execution"),
        "frame": {
            "session_id": identity["session_id"],
            "capture_frame_id": identity["capture_frame_id"],
            "present_index": identity["present_index"],
            "event_stream_sha256": identity["event_stream_sha256"],
        },
        "checkpoint": {
            "event": identity["checkpoint_event"],
            "resource": identity["resource"],
        },
        "draw": {"event": identity["draw_event"]},
        "input": {"package_sha256": request["input_package_sha256"]},
    }
    if execution.get("execution") == "completed":
        if artifact is None or not artifact.is_file():
            raise RendererError("the native executor produced no artifact")
        receipt["artifact"] = {
            "file": "result.bgra",
            "sha256": _sha256_file(artifact),
            "bytes": artifact.stat().st_size,
            "width": execution["artifact"]["width"],
            "height": execution["artifact"]["height"],
            "pixel_format": "BGRA8",
            "origin": "lower_left",
        }
        receipt["gl"] = execution.get("gl")
    else:
        receipt["unsupported"] = list(execution.get("missing") or [])
    body = dict(receipt)
    body.pop("signature", None)
    signature = gfx_ed25519.sign(seed, replay_input.canonical_json(body))
    receipt["signature"] = {
        "algorithm": "ed25519",
        "canonicalization": CANONICALISATION,
        "value": signature.hex(),
    }
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n",
                                     encoding="utf-8")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        request = json.loads(args.request.read_text(encoding="utf-8"))
        package_dir = Path(request["input_package"]).expanduser().resolve()
        verified = replay_input.verify_package(package_dir)
        if verified["package_sha256"] != request["input_package_sha256"]:
            raise RendererError("input package digest does not match the request")
        manifest = verified["manifest_document"]
        seed = _signing_seed()
        native = _native_executable()
        components = [
            _component("native-gl-executor", native),
            _component("replay-input-verifier",
                       Path(replay_input.__file__)),
            _component("receipt-signer", Path(gfx_ed25519.__file__)),
        ]
        native_dir = args.out / "native"
        native_dir.mkdir(parents=True, exist_ok=False)
        control = args.out / "control.ctl"
        control.write_text("\n".join(_ctl_lines(manifest, package_dir)) + "\n",
                           encoding="utf-8")
        completed = subprocess.run(
            [str(native), "--control", str(control), "--out", str(native_dir)],
            capture_output=True, text=True, check=False)
        execution_path = native_dir / "execution.json"
        if not execution_path.is_file():
            raise RendererError(
                "the native executor wrote no execution record"
                + (f": {completed.stderr.strip()[-500:]}"
                   if completed.stderr.strip() else ""))
        execution = json.loads(execution_path.read_text(encoding="utf-8"))
        if execution.get("schema") != EXECUTION_SCHEMA:
            raise RendererError("the native execution record schema is invalid")
        if execution.get("execution") not in ("completed",
                                               "unsupported_drawable"):
            raise RendererError(
                f"the native execution status is {execution.get('execution')!r}")
        if completed.returncode != 0:
            raise RendererError(
                f"the native executor failed with exit {completed.returncode}: "
                f"{execution.get('reason')}")
        artifact = native_dir / "result.bgra"
        if execution.get("execution") == "completed":
            if artifact.is_symlink() or not artifact.is_file():
                raise RendererError("the native artifact is not a regular file")
            expected = int(manifest["identity"]["width"]) * int(
                manifest["identity"]["height"]) * 4
            if artifact.stat().st_size != expected:
                raise RendererError("the native artifact has the wrong size")
            result = args.out / "result.bgra"
            result.write_bytes(artifact.read_bytes())
            artifact = result
        else:
            artifact = None
        _write_receipt(out=args.out, request=request, execution=execution,
                       artifact=artifact, renderer_path=Path(__file__).resolve(),
                       components=components, seed=seed)
        return 0
    except (RendererError, replay_input.ReplayInputError, OSError, TypeError,
            ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"gl-control-renderer: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
