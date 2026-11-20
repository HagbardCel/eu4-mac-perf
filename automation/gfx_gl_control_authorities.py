"""Checked-in authority registry for original-API control renderers.

Adding an entry is a reviewed code change. The qualification gate refuses Metal
blame for any renderer whose sha256 is absent here; a caller-supplied authority
file is not a pin and is not accepted.
"""

from __future__ import annotations

SCHEMA = "gfx-gl-control-renderer-authorities/v1"

# sha256 -> {"name", "api", "qualified", "qualified_by", "source"}
# Intentionally empty: no original-API control renderer has been reviewed and
# pinned at this head. The 904 control and any ad-hoc renderer therefore remain
# unqualified for causal Metal attribution.
AUTHORITIES: dict[str, dict] = {}
