Implement the four Metal index-buffer replacements described in the trusted contract.

Read this file. It is the contract. Do not modify it, and do not rediscover the contract from the binary:

`/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf/analysis/metal-init/index-buffers/note.md`

Implement that contract in the existing buffer files. Append the four `GFX_ENTRY` lines from the note. Keep `progress.md` updated with what you changed and what is still open.

Edit only these files:

- `benchmark/eu4_gfx_buffers.mm`
- `benchmark/eu4_gfx_buffers.h`
- `benchmark/eu4_gfx_entries.def`
- `progress.md`

Do not edit the note, the host, the patcher, the campaign, or any test. Do not add a second patcher, a GL-name identity, a command buffer, or a generic GPU layer. Do not commit, reset, or push.

The registry is a second table beside the vertex buffers, keyed by the engine header. Create copies the supplied bytes. Update replaces the whole allocation and keeps the old buffer until the new one exists. Destroy releases the GPU resource, updates `nGLIBCount` once, and recycles the header only as the note says. Bind stores the current index resource and writes master `+0x68`. An external checker decides when this is accepted.
