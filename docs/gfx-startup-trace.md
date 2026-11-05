# Original EU4 graphics startup trace

Passive OpenGL reference capture for `_GfxGetGraphicsSettings` (`0x15ed388`) and `_GfxInit` (`0x15e8ea0`) on the pinned GOG x86-64 executable. Schema `gfx-startup/v0`. Does not load Metal and does not replace the original functions.

## Offline

```
python3 -m unittest tests.test_gfx_startup_trace
```

## Live

```
python3 benchmark/eu4_gfx_startup_trace.py preflight
python3 benchmark/eu4_gfx_startup_trace.py run --authorize-live
```

Requires an explicit `--authorize-live` flag, the pinned executable, and a closed game process. Injects only the startup-trace dylib.

## Lifecycle

Recorder states: `ARMED` (admit new calls), `DRAINING` (refuse new entries, still record exits), `SEALED` (no admitted calls remain). Entry/exit callbacks stay installed for the process lifetime. Trampolines stay mapped until exit.

A qualifying `_GfxInit` has a readable success byte equal to `1`, a non-null master, and complete required master fields. Unsuccessful inits are diagnostics only. Settings may be `observed` or `not_observed` within the capture window.

## Outputs

`results/<timestamp>-gfx-startup/` contains `startup.status`, `events.jsonl`, `summary.json`, and `metadata.json`. Keep raw captures local; commit only a short evidence note with artifact hashes.
