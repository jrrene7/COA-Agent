import os
import stat
import tempfile
import unittest
from pathlib import Path

from tests.external_stubs import install

install()

from lib.persistence import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    RunStore,
)


class RunStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = RunStore(Path(self._tmp.name) / "runs.db")
        self.addCleanup(self.store.close)

    def test_start_run_records_running_status(self):
        self.store.start_run("run-1", "Acme", "https://acme.example")

        row = self.store.get_run("run-1")
        self.assertEqual(row["status"], STATUS_RUNNING)
        self.assertEqual(row["company"], "Acme")
        self.assertIsNotNone(row["started_at"])

    def test_unknown_run_returns_none(self):
        self.assertIsNone(self.store.get_run("nope"))

    def test_snapshots_are_ordered_and_scoped_by_run_id(self):
        self.store.start_run("run-1", "Acme")
        self.store.start_run("run-2", "Other")
        self.store.record_snapshot("run-1", 1, "research_step", {"research_data": {"profile": "A"}})
        self.store.record_snapshot("run-2", 1, "research_step", {"research_data": {"profile": "B"}})
        self.store.record_snapshot("run-1", 2, "marketing_step", {"marketing_data": {"hook": "h"}})

        snaps = self.store.get_snapshots("run-1")
        self.assertEqual([s["step_id"] for s in snaps], ["research_step", "marketing_step"])
        self.assertEqual(snaps[0]["state"]["research_data"]["profile"], "A")

    def test_reconstruct_state_replays_snapshots_in_order(self):
        self.store.start_run("run-1", "Acme")
        self.store.record_snapshot("run-1", 1, "research_step", {"research_data": {"profile": "A"}})
        self.store.record_snapshot("run-1", 2, "sales_step", {"feedback_attempts": 1})
        self.store.record_snapshot("run-1", 3, "report_step", {"report_path": "/tmp/r.md"})

        state = self.store.reconstruct_state("run-1")
        self.assertEqual(state["research_data"]["profile"], "A")
        self.assertEqual(state["feedback_attempts"], 1)
        self.assertEqual(state["report_path"], "/tmp/r.md")

    def test_snapshot_scrubs_secret_shaped_strings(self):
        """Scraped content could carry a key; the store must not durably keep one."""
        self.store.start_run("run-1", "Acme")
        self.store.record_snapshot(
            "run-1", 1, "research_step", {"research_data": {"profile": "key sk-abcd1234efgh"}}
        )

        stored = self.store.get_snapshots("run-1")[0]["state"]
        self.assertNotIn("sk-abcd1234efgh", stored["research_data"]["profile"])
        self.assertIn("[REDACTED]", stored["research_data"]["profile"])

    def test_finish_run_persists_status_and_totals(self):
        self.store.start_run("run-1", "Acme")
        self.store.finish_run(
            "run-1",
            status=STATUS_COMPLETED,
            report_path="/tmp/acme.md",
            duration_seconds=12.5,
            totals={
                "llm_calls": 3,
                "prompt_tokens": 2700,
                "completion_tokens": 1000,
                "total_tokens": 3700,
                "estimated_cost_usd": 0.001005,
                "step_retries": 1,
                "feedback_loops": 2,
            },
        )

        row = self.store.get_run("run-1")
        self.assertEqual(row["status"], STATUS_COMPLETED)
        self.assertEqual(row["total_tokens"], 3700)
        self.assertEqual(row["feedback_loops"], 2)
        self.assertEqual(row["step_retries"], 1)
        self.assertAlmostEqual(row["estimated_cost_usd"], 0.001005)
        self.assertAlmostEqual(row["duration_seconds"], 12.5)

    def test_finish_run_redacts_secrets_in_error_text(self):
        self.store.start_run("run-1", "Acme")
        self.store.finish_run("run-1", status=STATUS_FAILED, error="bad key sk-livesecret99")

        self.assertNotIn("sk-livesecret99", self.store.get_run("run-1")["error"])

    def test_step_metrics_are_scoped_by_run_id(self):
        self.store.start_run("run-1", "Acme")
        self.store.record_step_metric("run-1", "research", "completed", 1.25)
        self.store.record_step_metric("run-1", "marketing", "failed", 0.5)
        self.store.record_step_metric("run-2", "research", "completed", 9.0)

        metrics = self.store.get_step_metrics("run-1")
        self.assertEqual([m["step"] for m in metrics], ["research", "marketing"])
        self.assertEqual(metrics[1]["status"], "failed")

    def test_recent_runs_returns_newest_first(self):
        for i in range(3):
            self.store.start_run(f"run-{i}", f"Company {i}")

        rows = self.store.recent_runs(limit=2)
        self.assertEqual(len(rows), 2)

    def test_database_file_is_owner_only(self):
        mode = stat.S_IMODE(os.stat(self.store.db_path).st_mode)
        self.assertEqual(mode, stat.S_IRUSR | stat.S_IWUSR)

    def test_survives_reopen(self):
        """Durability is the point: a new process must see prior runs."""
        db = Path(self._tmp.name) / "reopen.db"
        with RunStore(db) as first:
            first.start_run("run-1", "Acme")
            first.record_snapshot("run-1", 1, "research_step", {"research_data": {"profile": "A"}})

        with RunStore(db) as second:
            self.assertEqual(second.get_run("run-1")["company"], "Acme")
            self.assertEqual(second.reconstruct_state("run-1")["research_data"]["profile"], "A")


if __name__ == "__main__":
    unittest.main()
