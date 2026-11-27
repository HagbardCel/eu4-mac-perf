"""Historical sprite replay command."""

import sys

from automation import gfx_sprite_replay

if __name__ == "__main__":
    raise SystemExit(gfx_sprite_replay.main())

sys.modules[__name__] = gfx_sprite_replay
