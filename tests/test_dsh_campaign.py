"""Sidecar, policy and execution-root validation for DSH campaigns."""

import json
import tempfile
import unittest
from pathlib import Path

from automation.dsh_campaign import (
    SidecarError,
    load_sidecar,
    path_allowed,
)

REPO = Path(__file__).resolve().parents[1]
SMOKE = "tests/fixtures/dsh/smoke-campaign/campaign.json"


class SidecarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, **overrides) -> Path:
        data = {
            "schema": "eu4-dsh-campaign/v1",
            "campaign_id": "dsh-unit",
            "source_campaign": SMOKE,
            "run_root": str(self.root / "run"),
            "candidate": {
                "mode": "seed",
                "workspace": str(self.root / "ws"),
                "policy": {"allowed_paths": ["marker.txt", "progress.md"]},
            },
            "checker_retry": {"mode": "stateful", "confirmation_rounds": 1},
            "goal": {"max_rounds": 4},
            "limits": {"max_validations": 4, "validation_timeout_seconds": 30,
                       "wall_clock_seconds": 600},
            "live_capture": {"enabled": False},
        }
        for key, value in overrides.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        path = self.root / "sidecar.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_reads_a_campaign_that_has_no_opencode_contract(self) -> None:
        sidecar = load_sidecar(self.write())
        campaign = json.loads((REPO / SMOKE).read_text(encoding="utf-8"))
        self.assertNotIn("runtime", campaign)
        self.assertNotIn("opencode_version", campaign)
        self.assertEqual(sidecar.campaign_id, "dsh-unit")
        self.assertEqual(sidecar.mode, "seed")
        self.assertEqual(sidecar.retry_mode, "stateful")
        self.assertEqual(sidecar.checker.name, "accept.py")
        self.assertTrue(sidecar.objective_text.startswith("# DSH fixture campaign"))
        self.assertEqual(len(sidecar.objective_digest), 64)

    def test_rejects_unknown_schema(self) -> None:
        with self.assertRaises(SidecarError):
            load_sidecar(self.write(schema="eu4-dsh-campaign/v0"))

    def test_rejects_missing_campaign_id(self) -> None:
        with self.assertRaises(SidecarError):
            load_sidecar(self.write(campaign_id=""))

    def test_rejects_path_style_campaign_id(self) -> None:
        with self.assertRaises(SidecarError):
            load_sidecar(self.write(campaign_id="../escape"))

    def test_rejects_live_capture(self) -> None:
        with self.assertRaises(SidecarError) as caught:
            load_sidecar(self.write(live_capture={"enabled": True}))
        self.assertIn("exclusivity", str(caught.exception))

    def test_stateful_requires_a_confirmation_round(self) -> None:
        with self.assertRaises(SidecarError) as caught:
            load_sidecar(self.write(
                checker_retry={"mode": "stateful", "confirmation_rounds": 0}
            ))
        self.assertIn("confirmation_rounds", str(caught.exception))

    def test_deterministic_rejects_confirmation_rounds(self) -> None:
        with self.assertRaises(SidecarError):
            load_sidecar(self.write(
                checker_retry={"mode": "deterministic", "confirmation_rounds": 1}
            ))

    def test_worktree_mode_requires_a_baseline(self) -> None:
        with self.assertRaises(SidecarError) as caught:
            load_sidecar(self.write(
                candidate={"mode": "worktree", "workspace": str(self.root / "wt")},
            ))
        self.assertIn("candidate_baseline_commit", str(caught.exception))

    def test_baseline_must_match_the_campaign_pin(self) -> None:
        with self.assertRaises(SidecarError) as caught:
            load_sidecar(self.write(
                source_campaign="campaigns/eu4-re2-pilot/campaign.json",
                candidate_baseline_commit="0" * 40,
                candidate={"mode": "worktree", "workspace": str(self.root / "wt")},
            ))
        self.assertIn("must not be substituted", str(caught.exception))

    def test_run_root_inside_the_workspace_is_rejected(self) -> None:
        # A workspace nested in the run root is the normal layout; a run root
        # nested inside the workspace is not, and would let the candidate own
        # its own receipts.
        outer = self.root / "outer"
        with self.assertRaises(SidecarError):
            load_sidecar(self.write(
                run_root=str(outer / "run"),
                candidate={"mode": "seed", "workspace": str(outer),
                           "policy": {"allowed_paths": ["marker.txt"]}},
            ))

    def test_run_root_under_opencode_state_is_rejected(self) -> None:
        with self.assertRaises(SidecarError) as caught:
            load_sidecar(self.write(run_root=str(REPO / ".local" / "automation" / "x")))
        self.assertIn(".local/automation", str(caught.exception))

    def test_seed_policy_is_derived_when_absent(self) -> None:
        sidecar = load_sidecar(self.write(
            candidate={"mode": "seed", "workspace": str(self.root / "ws")},
        ))
        self.assertIn("marker.txt", sidecar.allowed_paths)
        self.assertIn("progress.md", sidecar.allowed_paths)

    def test_policy_file_is_read_and_digested(self) -> None:
        policy = REPO / "campaigns" / "eu4-l32b-bootstrap" / "infra.json"
        sidecar = load_sidecar(self.write(
            candidate={
                "mode": "seed",
                "workspace": str(self.root / "ws"),
                "policy": {"policy_file": str(policy)},
            },
        ))
        self.assertIn("progress.md", sidecar.allowed_paths)
        self.assertIn("automation/gfx_draw_accept.py", sidecar.forbidden_paths)
        self.assertEqual(len(sidecar.policy_digest), 64)

    def test_missing_objective_is_rejected(self) -> None:
        campaign = self.root / "campaign"
        campaign.mkdir()
        (campaign / "campaign.json").write_text(
            json.dumps({"objective": "missing.md", "accept": ["accept.py"]}),
            encoding="utf-8",
        )
        (campaign / "accept.py").write_text("print('{}')\n", encoding="utf-8")
        with self.assertRaises(SidecarError):
            load_sidecar(self.write(
                source_campaign=str(campaign / "campaign.json"),
                candidate={"mode": "seed", "workspace": str(self.root / "ws"),
                           "policy": {"allowed_paths": ["marker.txt"]}},
            ))


class PathAllowlistTests(unittest.TestCase):
    def test_matches_the_trusted_implementation(self) -> None:
        try:
            from automation.gfx_draw_accept import path_allowed as trusted
        except Exception as exc:  # pragma: no cover - benchmark deps absent
            self.skipTest(f"trusted matcher unavailable: {exc}")
        patterns = ["progress.md", "automation/gfx_contracts/", "docs/decisions/0004-*.md"]
        samples = [
            "progress.md",
            "progress.md.bak",
            "automation/gfx_contracts/api.py",
            "automation/gfx_contracts",
            "docs/decisions/0004-x.md",
            "docs/decisions/0005-x.md",
            "other/file.py",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertEqual(
                    path_allowed(sample, patterns), trusted(sample, patterns)
                )


if __name__ == "__main__":
    unittest.main()


class WorkspaceBaseTests(unittest.TestCase):
    """The candidate is agent-writable; the records must not be."""

    def test_the_tracked_sidecars_split_candidate_from_records(self) -> None:
        from automation.dsh_campaign import load_sidecar

        for name in ("offline-smoke", "trusted-validation", "multiround-smoke"):
            with self.subTest(sidecar=name):
                sidecar = load_sidecar(REPO / "dsh" / "campaigns" / f"{name}.json")
                self.assertEqual(sidecar.mode, "seed")
                # candidate inside the trusted checkout, where the sandbox reaches
                self.assertTrue(
                    str(sidecar.workspace).startswith(str(REPO / ".local")),
                    sidecar.workspace,
                )
                # qualification records outside it, in the host state root
                self.assertTrue(
                    str(sidecar.run_root).startswith(str(Path.home() / ".local" / "share")),
                    sidecar.run_root,
                )
                self.assertFalse(
                    str(sidecar.run_root).startswith(str(sidecar.workspace)),
                    "records must not live under the candidate workspace",
                )

    def test_an_unknown_workspace_base_is_rejected(self) -> None:
        import json
        import tempfile

        from automation.dsh_campaign import SidecarError, load_sidecar

        with tempfile.TemporaryDirectory() as tmp:
            data = json.loads(
                (REPO / "dsh" / "campaigns" / "offline-smoke.json").read_text(
                    encoding="utf-8"
                )
            )
            data["candidate"]["workspace_base"] = "elsewhere"
            path = Path(tmp) / "sidecar.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(SidecarError) as caught:
                load_sidecar(path)
        self.assertIn("workspace_base", str(caught.exception))
