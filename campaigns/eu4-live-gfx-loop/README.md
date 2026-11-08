# Live graphics replay experiment

This campaign tests whether an agent can use the committed authentic viewport
capture to diagnose and repair a controlled Metal discrepancy **without
launching EU4 again**.

The operator must:

1. create a commit containing the L1/L2 tools, the committed capture at
   `analysis/gfx-ops/v0/capture/`, and a deliberately wrong
   `eu4_gfx_set_viewport` implementation;
2. copy `campaign.example.json` to `campaign.json` and replace the workspace
   commit with that pin;
3. start the campaign with `python3 -m automation.cli run ...`.

The checker runs the offline test set and the committed-capture replay. It does
not need a live launch and does not consume the campaign launch counter. A
`pass` means the candidate tests pass and the replay establishes the declared
`function_cpu_state` scope. Independent PR review remains the merge gate.
