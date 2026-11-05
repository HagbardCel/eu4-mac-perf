# Metal shadow worker

A persistent x86-64 process that calls the existing Metal gfx functions. It does not attach to EU4, does not reimplement the renderer, and does not claim original-game equivalence.

Build and speak the protocol through [benchmark/eu4_gfx_shadow_worker.py](../benchmark/eu4_gfx_shadow_worker.py). The binary links [eu4_gfx_init.mm](../benchmark/eu4_gfx_init.mm), [eu4_gfx_buffers.mm](../benchmark/eu4_gfx_buffers.mm), and [eu4_metal_events.c](../benchmark/eu4_metal_events.c). Stdout is one JSON object per line. Diagnostics and the receipt directory go to stderr.

## Envelope

Schema `metal-shadow/v0`. The first valid envelope uses `seq` 1. Each later valid envelope uses the previous response's `next`.

A valid envelope has that schema, an integer `seq` equal to the expected value, a string `op`, and an object `args`. Integers are JSON numbers with no fraction and no boolean. Malformed JSON, a wrong schema, a non-integer `seq`, the wrong `seq`, or an input line with an embedded NUL returns `ok: false` and leaves `next` unchanged. A valid envelope whose operation is rejected also returns `ok: false`, and `next` advances. The graphics function is not called.

## Calls

`set_blend`, `set_depth`, `set_raster`, `update_vertex_buffer`, `update_vertex_buffer_multiple`, and `set_vertex_buffers` receive a pointer to the worker's master pointer. `shutdown`, `reset`, `set_viewport`, and `on_reset_master` receive the master. `on_reset_master` is the master object: its implementation loads the first qword, which init set to the master itself. `init` receives the 32-byte window record, not the master pointer.

`set_blend`, `set_depth`, and `set_raster` take no state blob. They reapply the objects init installed. `usage` is passed through and ignored. `set_vertex_buffers` passes null offsets and words.

Resource IDs match `[A-Za-z0-9_]{1,64}` and are never reused, including after destroy. Shutdown is rejected while any buffer is live. A rejected shutdown does not call `eu4_gfx_shutdown`.

`create_vertex_buffer` requires `data_base64`. A string must decode to exactly `count * stride` bytes. JSON `null` passes a null data pointer. An absent field is rejected. The logical size, including a null create, must be at most 64KiB.

`update_vertex_buffer` sizes a `count <= 0` from the header count at `+4`, and the payload must be that many bytes. The original `count` is what the function receives. `partial` is 0 or 1. For `partial` 1, `offset` and the byte count must fit the current buffer without wrapping. `update_vertex_buffer_multiple` lays ranges down from byte 0. Each payload must equal its element count times the header stride, and the running total must fit.

Init succeeds only when the success byte is 1, the return value is the published slot, phase is 2, and the backend token is present. Any other result is `init_failed`: the process exits non-zero and does not retry. `configure_window` with `window_id` 0 makes the stand-in window return NULL, which is how a test reaches that path. Shutdown does not clear phase.

## Observations

A normal response has `result`, `live_count`, and compact master fields: dimensions, primitive, max texture, lod, viewport, dirty byte, and the three snap images. Slots carry a resource ID and length. Buffer bytes are not included.

`observe` lists every live ID and reads requested resources and slots through the existing inspectors. `header` is the 12-byte engine header: stride at `+0`, count at `+4`, token at `+8`. `buffer` is the MTLBuffer length and, only when `contents_known` is true, its bytes. A null create is not known. A full `partial` 0 update, or a multiple-range update that covers the buffer from byte 0, marks it known. A partial update does not change the flag.

There is no command-buffer hook and no per-byte validity map.

A failed read of a known buffer, or a mapped header whose length cannot be read, is `observation_failed`. That reply is recoverable: the process stays up and the next command is answered. An empty slot is still a successful empty observation. Unknown contents omit bytes only after the length lookup succeeds.

If a command has already called the graphics function and the following state capture finds a selected header missing from the production registry or the logical map, the reply is `ok: false`, `error: observation_failed`, `executed: true`, and the process exits 1. That reply has no `state`.

`state` includes `vertex_records` and `vertex_gpus` from the production registry, including before init and after shutdown.

A failure to allocate the input line, or an I/O error on stdin, prints `read allocation` or `read error` to stderr and exits 1. A clean EOF exits 0.
