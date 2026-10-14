# Agent pilot review

## Run metadata

- Baseline revision: `auto-v1-20261013` (required for production pilot; fixture pilot used `graph_mini` via `EU4_RE_EXPORT`)
- Investigation id: `test-inv` / `pilot` (automated); replace for live run
- Model profiles (discovery / reconstruction / verification): per `reverse_engineering/graphics_agent/config/models.json`
- Prompt SHAs: stored in session checkpoint meta (`system_prompt_sha256`)
- Tool call count / Ghidra extractions / estimated cost: not measured on live corpus in CI

## Baseline reproduction

- Gfx draw path recovered (Y/N): **Y (fixture)** — `0x5000 → 0x7000 → ext:_glDrawElements` (`tests/test_graphics_agent_paths.py`)
- Thunk / collapsed-edge qualifications noted (Y/N): **N/A (fixture)** — live pilot must note thunk vs direct on real inventory

## New discovery or blocker

- Finding or explicit blocker: **Automated only** — evidence round-trip + claim storage; live semantic Ghidra finding **pending operator run**
- Evidence IDs: from `find_paths` call-edge registry (fixture)

## Verification

- Unsupported fixture claim rejected (Y/N): **Y** — `tests/test_graphics_agent_verification.py` via `run_verification` + evidence resolver
- model_verified vs human_accepted: claims stored as `evidence_validated`; verifier sets `semantic_review_passed` or `rejected` separately (`human_accepted` not automated)

## Efficiency

- Duplicate Ghidra ops on resume (Y/N): **Y (fixture)** — published cache dedup (`tests/test_graphics_agent_ghidra.py`)
- Coverage delta: not measured on live corpus

## Resume / session

- PydanticAI `message_history` persisted (`tests_agent/test_runner_resume.py`)

## Human acceptance

- [ ] Significant claims human_accepted (live pilot)
- [ ] Complete live cycle on `auto-v1-20261013`: retrieve evidence, Ghidra acquisition, claim, verification, resume without duplicate headless

## Operator checklist (live pilot)

1. `prepare_package` / existing agent context on `auto-v1-20261013`
2. Run discovery → reconstruct with real Ghidra project metadata present
3. Submit one `supported_inference` with evidence IDs; run `verify` mode on claim id
4. Resume session; confirm no second headless for same idempotency key
5. Update this file with real evidence IDs and outcomes
