"""Shared graphics-observation schema and attachment-format identity.

One place defines what a captured render-target observation means: its exact
byte layout, whether a stored alpha channel exists, and therefore which
channels a candidate may be compared on.  The native capture writer, the frame
graph, the portable case builder and the checkpoint reconciler all import this
module, so a format cannot be interpreted differently by different layers.

The declared record is evidence to be checked, never the authority.  The
attachment's internal format and component sizes decide the scope; a record
that disagrees fails closed.
"""

from __future__ import annotations

# One authoritative observation record per line of the observation file.
OBSERVATION_SCHEMA = "gfx-observation/v1"
OBSERVATIONS_SCHEMA = "gfx-observations/v1"
# Accepted legacy names.  They describe the same record; a sealed frame that
# already used one remains readable.
LEGACY_OBSERVATION_SCHEMAS = ("gfx-prefix-observation/v1",)
LEGACY_OBSERVATIONS_SCHEMAS = ("gfx-prefix-observations/v1",)

DEFAULT_INTERNAL_FORMAT = 0  # no attachment identity: the default drawable

GL_RGB8 = 0x8051
GL_RGBA8 = 0x8058
GL_SRGB8_ALPHA8 = 0x8C43
GL_R32F = 0x822E
GL_UNSIGNED_NORMALIZED = 0x8C17
GL_FLOAT = 0x1406

# internal format -> (byte layout, compared channels, alpha, component bits,
# component type).  A layout names the exact bytes the readback produces:
# BGRA8 and RGBA8 are 4x8-bit; R32F is one 32-bit float.
_FORMATS = {
    GL_RGB8: {
        "pixel_format": "BGRA8",
        "channels": "RGB",
        "alpha_applicable": False,
        "bits": (8, 8, 8, 0),
        "component_type": GL_UNSIGNED_NORMALIZED,
    },
    GL_RGBA8: {
        "pixel_format": "RGBA8",
        "channels": "RGBA",
        "alpha_applicable": True,
        "bits": (8, 8, 8, 8),
        "component_type": GL_UNSIGNED_NORMALIZED,
    },
    GL_SRGB8_ALPHA8: {
        "pixel_format": "BGRA8",
        "channels": "RGBA",
        "alpha_applicable": True,
        "bits": (8, 8, 8, 8),
        "component_type": GL_UNSIGNED_NORMALIZED,
    },
    GL_R32F: {
        "pixel_format": "R32F",
        "channels": "R32F",
        "alpha_applicable": False,
        "bits": (32, 0, 0, 0),
        "component_type": GL_FLOAT,
    },
}

DEFAULT_FORMAT = {
    "pixel_format": "BGRA8",
    "channels": "RGBA",
    "alpha_applicable": True,
    "bits": (8, 8, 8, 8),
    "component_type": GL_UNSIGNED_NORMALIZED,
}


def _internal_format(observation: dict) -> int:
    identity = observation.get("target_identity")
    if not isinstance(identity, dict):
        return -1
    value = identity.get("internal_format")
    if isinstance(value, bool) or not isinstance(value, int):
        if value is None:
            return -1
        try:
            return int(value)
        except (TypeError, ValueError):
            return -1
    return value


def _int_field(identity: dict, name: str):
    value = identity.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def format_scope(observation: dict) -> dict:
    """Return the authoritative comparison scope for one observation.

    ``pixel_format`` is the exact byte layout the capture must have written.
    ``compared_channels`` is ``RGB``, ``RGBA`` or ``R32F``.  A record that
    declares a different layout, a different alpha applicability, an impossible
    component size or an unknown internal format produces a problem and is
    never silently normalized.
    """
    if not isinstance(observation, dict):
        return {
            "pixel_format": None,
            "alpha_applicable": True,
            "compared_channels": "RGBA",
            "supported": False,
            "problems": ["observation is not an object"],
        }
    internal = _internal_format(observation)
    identity = observation.get("target_identity")
    if not isinstance(identity, dict):
        identity = {}
    problems: list[str] = []
    if internal == -1:
        problems.append("observation has no attachment format identity")
        internal = DEFAULT_INTERNAL_FORMAT
    if internal == DEFAULT_INTERNAL_FORMAT:
        # The default drawable has no attachment query; its channel sizes are
        # the drawable's queried identity.  When alpha size is absent the
        # scope stays RGBA so missing evidence can never narrow it.
        alpha_bits = _int_field(identity, "alpha_bits")
        declared_alpha = observation.get("alpha_applicable")
        channel_names = ("red_bits", "green_bits", "blue_bits", "alpha_bits")
        channel_bits = [_int_field(identity, name) for name in channel_names]
        # A default-drawable record written before the channel query has every
        # size at zero and no declared applicability.  Keep its historical
        # four-channel scope rather than relaxing it.
        legacy_unknown = (declared_alpha is None
                          and all(value in (None, 0)
                                  for value in channel_bits))
        if legacy_unknown:
            alpha = True
        elif alpha_bits is None or alpha_bits > 0:
            alpha = True
        elif declared_alpha is None:
            alpha = True
        else:
            alpha = False
        expected = dict(DEFAULT_FORMAT)
        expected["alpha_applicable"] = alpha
        expected["channels"] = "RGBA" if alpha else "RGB"
        expected["bits"] = (8, 8, 8, 8 if alpha else 0)
        if not legacy_unknown:
            for index, name in enumerate(channel_names):
                value = channel_bits[index]
                if value is not None and value != expected["bits"][index]:
                    problems.append(
                        "default drawable %s=%d does not match the drawable "
                        "channel identity" % (name, value))
    else:
        expected = _FORMATS.get(internal)
        if expected is None:
            return {
                "pixel_format": None,
                "alpha_applicable": True,
                "compared_channels": "RGBA",
                "supported": False,
                "problems": [
                    "unsupported or ambiguous attachment internal format "
                    "0x%x" % internal],
            }
        for index, name in enumerate(("red_bits", "green_bits", "blue_bits",
                                      "alpha_bits")):
            value = _int_field(identity, name)
            if value is not None and value != expected["bits"][index]:
                problems.append(
                    "%s=%d does not match internal format 0x%x"
                    % (name, value, internal))
        component = _int_field(identity, "component_type")
        if component is not None and component != expected["component_type"]:
            problems.append(
                "component type 0x%x does not match internal format 0x%x"
                % (component, internal))
    declared_format = observation.get("pixel_format")
    if declared_format is not None and declared_format != expected["pixel_format"]:
        problems.append(
            "declared pixel format %r does not match the attachment layout %r"
            % (declared_format, expected["pixel_format"]))
    colour = observation.get("color")
    if isinstance(colour, dict):
        colour_format = colour.get("pixel_format")
        if (colour_format is not None
                and colour_format != expected["pixel_format"]):
            problems.append(
                "colour pixel format %r does not match the attachment layout "
                "%r" % (colour_format, expected["pixel_format"]))
    declared_alpha = observation.get("alpha_applicable")
    if declared_alpha is not None and (
            (declared_alpha is not False) != expected["alpha_applicable"]):
        problems.append(
            "alpha applicability contradicts the sealed attachment identity")
    return {
        "pixel_format": expected["pixel_format"],
        "alpha_applicable": expected["alpha_applicable"],
        "compared_channels": expected["channels"],
        "supported": not problems,
        "problems": problems,
    }


def is_supported_observation_schema(value) -> bool:
    return (value == OBSERVATION_SCHEMA
            or value in LEGACY_OBSERVATION_SCHEMAS)


def is_supported_observations_schema(value) -> bool:
    return (value == OBSERVATIONS_SCHEMA
            or value in LEGACY_OBSERVATIONS_SCHEMAS)


def authority(manifest: dict | None, seal: dict | None = None) -> str:
    """Classify the provenance of one frame without borrowing authority.

    ``authentic`` is an original sealed capture; ``derived`` reproduces part of
    one; ``synthetic`` is a controlled fixture with no original-frame claim.
    """
    manifest = manifest if isinstance(manifest, dict) else {}
    seal = seal if isinstance(seal, dict) else {}
    if manifest.get("derived_from") or seal.get("derived_from"):
        return "derived"
    if manifest.get("test_only") or seal.get("test_only"):
        return "synthetic"
    prefix = manifest.get("prefix")
    if isinstance(prefix, dict) and prefix.get("authority") == "synthetic":
        return "synthetic"
    return "authentic"
