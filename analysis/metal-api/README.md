# Local Metal API contracts

These notes are the versioned Metal contracts for OpenGL-to-Metal reconstruction. They are written for this tree. They are not a copy of Apple's documentation, and they do not include SDK headers.

`index.md` is the lookup table. `sources.json` records which contract was reviewed, the SDK version used, the headers read, and the Apple URLs opened. `reviewed` means an editor labeled the sentences. It does not prove that a source supports each sentence. The labels inside a contract do that job:

- `SDK` — the installed header says it.
- `Apple docs` — an official page was opened and says it.
- `Project-derived` — this repository's code, tests, or prior research.
- `Unresolved` — not established.

## What is installed here

Checked 2026-11-05. `xcodebuild -version` fails because the active developer directory is Command Line Tools, not Xcode.app. `xcrun --sdk macosx --show-sdk-version` is 27.0 and the SDK path is the Command Line Tools MacOSX SDK. Metal headers are in that SDK under `System/Library/Frameworks/Metal.framework/Headers`. No Xcode documentation cache and no Metal `.doccarchive` were present. `xcodebuild docbuild` exports documentation for a developer project. It does not export Apple's framework docs.

Signatures and header comments come from that SDK. A behavioral sentence the header does not state is `Apple docs` only after the cited page is opened, and otherwise `Unresolved`. There is no downloader and no scraper.

## Search

From the repository root:

```sh
rg -n -i 'setScissorRect|glScissor' analysis/metal-api
rg -n -i 'MTLBuffer|glBufferSubData' analysis/metal-api
rg -n -i 'loadAction|glClear' analysis/metal-api
```

## Updating a contract

Re-read the header at `xcrun --sdk macosx --show-sdk-path`. Re-open each URL you still cite. Set `reviewed` and `sdk_version` in `sources.json` to the versions you actually used. Add or change a label only from that reading. Do not mark a sentence `SDK` or `Apple docs` from memory, and do not treat a reachable URL as proof by itself.

If a page or header is missing, leave the sentence `Unresolved`. Reconstruction continues from the contracts and headers that are present. `.local/metal-docs/` is an optional place for locally obtained reference material. `.local/` is gitignored, so that directory is not part of the corpus. The contracts stay usable when it is absent.

A reconstruction campaign sees this directory only when its pinned commit contains it. Older campaign files keep the checker bytes and research notes they recorded. The skill that tells an agent to read the index is `.opencode/skills/opengl-metal-reconstruction/SKILL.md`.
