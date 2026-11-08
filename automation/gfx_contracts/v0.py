"""Contract v0: no field-specific validators beyond the trusted base set.

The four base fields (draw invocation, primitive semantics, viewport, render
target) and the three already-qualified resource fields are validated by
``automation.gfx_draw_accept.BASE_VALIDATORS``. A later version adds validators
for newly evidenced fields through the qualification campaign.
"""

from __future__ import annotations

VALIDATORS = {}
