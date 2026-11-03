"""Campaign loading, atomic state, and the campaign lock."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from automation.state import campaign_lock, load_campaign, load_state, save_state


class CampaignTests(unittest.TestCase):
    def test_loads_json_campaign(self) -> None:
        campaign = load_campaign(Path("campaigns/automation-smoke/campaign.json"))
        self.assertEqual(campaign.id, "automation-smoke")
        self.assertEqual(campaign.model, "opencode/big-pickle")
        self.assertEqual(campaign.opencode_version, "2.2.5")
        self.assertTrue(campaign.checker.name == "accept.py")
        self.assertIn("ready", campaign.objective_text)

    def test_state_round_trip_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.json"
            save_state(path, {"status": "running", "pending_submission_id": "msg_a"})
            self.assertFalse(path.with_suffix(".json.tmp").exists())
            self.assertEqual(load_state(path)["pending_submission_id"], "msg_a")

    def test_second_process_cannot_take_the_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "campaign.lock"
            ready = Path(temporary) / "ready"
            holder = subprocess.Popen(
                [sys.executable, "-c",
                 "import fcntl,sys,time; h=open(sys.argv[1],'a+');"
                 "fcntl.flock(h.fileno(), fcntl.LOCK_EX);"
                 "open(sys.argv[2],'w').write('1'); time.sleep(30)",
                 str(path), str(ready)],
            )
            try:
                for _ in range(50):
                    if ready.exists():
                        break
                    time.sleep(0.05)
                with self.assertRaises(RuntimeError) as caught:
                    with campaign_lock(path):
                        pass
                self.assertIn("campaign lock held", str(caught.exception))
            finally:
                holder.kill()
                holder.wait()


if __name__ == "__main__":
    unittest.main()
