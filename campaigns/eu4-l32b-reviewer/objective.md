# L3.2b independent graphics reviewer

Inspect the pinned candidate worktree (HEAD is the frozen candidate commit).
The candidate implements L3.2b-I effective-state capture for the authentic
Simple draw at caller RVA `0xedfcd1`.

Verify, against the frozen L3.2a capture/packet and the candidate's new
same-session capture/packet:

- the selected draw still satisfies Decision 0003;
- every claimed input is supported by original-execution evidence, exact bytes
  and hashes, with the claimed resource/shader/texture identity actually
  associated with the selected draw;
- no input was synthesized, inferred from an unrelated object, or promoted
  without evidence;
- capture/session/observer-build provenance and the launch receipt are coherent;
- the L3.2a oracle and campaign/checker files are unchanged;
- no gateway expansion, Metal execution, or fabricated `packet_ready` claim
  occurred.

Semantic interpretation of newly reconstructed layouts is your responsibility;
the deterministic checker only proves structure, association, bytes and
provenance. Be adversarial about object identity and same-session ordering.

The corrected checker specifically requires: the matrix to match the original
`_GfxSetConstantBuffer` record/header/call id and its captured 64 bytes; vertex
contents to be `resource_lifecycle` bytes resolved through the draw master's
buffer header to the preceding create record; binding resource ids and creation
call ids to match that record; and resource/draw evidence payloads to equal the
original captured bytes, not merely a self-consistent hash. Inspect the forged-
evidence negative tests and decide whether they cover the false-qualification
paths.

Write a validated `eu4-review/v1` JSON report to `EU4_REVIEW_REPORT_PATH`
(outside this worktree), with `candidate_commit` equal to HEAD,
`oracle_manifest_sha256` equal to `EU4_REVIEW_EXPECTED_ORACLE`, and
`outcome` `qualified|rejected|unresolved`. Do not modify the candidate worktree
except for optional `progress.md` notes; prefer writing only the external report.
