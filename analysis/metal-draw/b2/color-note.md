# Color acquisition, 2026-11-01

Session `fixtures/b2-color/20261101T133219Z` was the authorized capture on GOG EU4 v1.37.5.0 Inca (491d). The executable SHA-256 was `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`. The historical vertex matched `ea46e32:results/20261031T144434Z-metal-draw/matched.glsl`. The process exited 0. Status is `acquisition-failed`.

The operator opened Options on the main menu and closed it, then acknowledged `menu-options`. No fixture was present. The same process continued the Lübeck single-player save dated 25 January 1609, opened the Esc menu once the map was up, and closed it. That step was acknowledged as `ingame-esc`.

The recorder saw 17,080,117 indexed `glDrawElements` submissions and 22 programs. Every indexed draw was rejected as `not-color`. `candidates` and `qualified` stayed 0. No fixture was written, so there is no GL–Metal compare. The main menu accounted for 5 of the 22 programs. The map and Esc menu raised the count to 22.

B2 is not accepted. `docs/status.md` is unchanged. The matcher, the fixed-state checks, and the 2048 viewport cap were not widened.
