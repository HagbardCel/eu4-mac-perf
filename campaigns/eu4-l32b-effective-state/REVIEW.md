# L3.2b independent review sequence

The first L3.2b campaign uses a documented manual handoff. Do not build a
generic reviewer adapter before a successful campaign shows where automation is
actually needed.

After the implementer runner reports `pass`:

1. Snapshot the allowlisted candidate worktree:

   ```sh
   python3 -m automation.gfx_candidate_snapshot snapshot \
     --workspace .local/automation/eu4-l32b-effective-state/workspace \
     --campaign campaigns/eu4-l32b-effective-state/campaign.json \
     --round 1 \
     --output .local/automation/eu4-l32b-effective-state/candidate-r1.json
   ```

   The helper verifies the detached pin, source allowlist, frozen paths and
   verification manifest, then creates a candidate commit/ref parented on
   `1b5a82a`. It does not run any agent.

2. Materialize an independent reviewer campaign from the tracked template:

   ```sh
   python3 -m automation.gfx_candidate_snapshot reviewer \
     --campaign campaigns/eu4-l32b-effective-state/campaign.json \
     --candidate-json .local/automation/eu4-l32b-effective-state/candidate-r1.json \
     --round 1
   ```

   The helper copies `campaigns/eu4-l32b-reviewer/`, writes the candidate pin,
   creates a detached reviewer worktree, and prints the report path and the
   required `EU4_REVIEW_*` environment values.

3. Run the reviewer campaign with the printed environment:

   ```sh
   EU4_REVIEW_REPORT_PATH=<printed> \
   EU4_REVIEW_EXPECTED_CANDIDATE=<printed> \
   EU4_REVIEW_EXPECTED_ORACLE=<printed> \
   OPENCODE_URL=$OPENCODE_URL OPENCODE_PASSWORD=$OPENCODE_PASSWORD \
   python3 -m automation.cli run .local/automation/eu4-l32b-review-.../reviewer/campaign.json
   ```

   The reviewer campaign's checker passes when a valid `eu4-review/v1` report
   exists outside the candidate worktree. The reviewer is a separate OpenCode
   session; it never edits the candidate.

4. Validate the report directly:

   ```sh
   python3 - <<'PY'
   import json
   from pathlib import Path
   from automation.review_result import load_review, validate_review
   review = load_review(Path("<printed report>"))
   print(review["outcome"])
   print(validate_review(
       review,
       expected_candidate_commit="<candidate commit>",
       expected_oracle_manifest_sha256="<manifest digest>",
   ))
   PY
   ```

   `qualified` permits PR assembly. `rejected` findings are fed into a new
   fixed-objective remediation campaign seeded from the rejected candidate; do
   not mutate the completed campaign. `unresolved` requires a human decision.

5. Integrate the reviewed candidate commit into the campaign branch and open
   the PR with the review report and checker evidence. Human merge remains the
   final gate.
