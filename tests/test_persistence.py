import os
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.external_stubs import install

install()

from lib.routing import sla_due_at
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

class SlaTrackingTests(unittest.TestCase):
    """Recording who owns an escalation is only half of it — whether they
    actually responded is the half that makes the number mean anything."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = RunStore(Path(self._tmp.name) / "runs.db")
        self.addCleanup(self.store.close)

    def _escalated(self, run_id, company="Acme", due=None, priority="urgent"):
        self.store.start_run(run_id, company)
        self.store.finish_run(
            run_id,
            status=STATUS_COMPLETED,
            totals={
                "escalations": 1,
                "queue": "escalation",
                "priority": priority,
                "owner": "support_lead",
                "sla_due_at": due or sla_due_at(priority),
            },
        )

    def test_open_escalations_lists_unanswered_only(self):
        self._escalated("run-1")
        self.store.start_run("run-2", "Calm Co")
        self.store.finish_run("run-2", status=STATUS_COMPLETED, totals={"escalations": 0})

        open_ids = [r["run_id"] for r in self.store.open_escalations()]
        self.assertEqual(open_ids, ["run-1"])

    def test_marking_responded_closes_it(self):
        self._escalated("run-1")
        self.assertTrue(self.store.mark_responded("run-1", "dana"))
        self.assertEqual(self.store.open_escalations(), [])
        self.assertEqual(self.store.get_run("run-1")["responded_by"], "dana")

    def test_marking_responded_twice_reports_no_change(self):
        """The second call must not overwrite the original response time."""
        self._escalated("run-1")
        self.store.mark_responded("run-1", "dana")
        first = self.store.get_run("run-1")["responded_at"]

        self.assertFalse(self.store.mark_responded("run-1", "someone-else"))
        self.assertEqual(self.store.get_run("run-1")["responded_at"], first)
        self.assertEqual(self.store.get_run("run-1")["responded_by"], "dana")

    def test_unknown_run_reports_no_change_rather_than_silently_succeeding(self):
        self.assertFalse(self.store.mark_responded("does-not-exist"))

    def test_open_escalations_are_ordered_most_overdue_first(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        soon = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        self._escalated("run-soon", "Later Co", due=soon)
        self._escalated("run-past", "Overdue Co", due=past)

        self.assertEqual(
            [r["run_id"] for r in self.store.open_escalations()],
            ["run-past", "run-soon"],
        )


class SlaTargetTests(unittest.TestCase):
    def test_urgent_has_the_tightest_target(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        urgent = datetime.fromisoformat(sla_due_at("urgent", now))
        normal = datetime.fromisoformat(sla_due_at("normal", now))
        self.assertLess(urgent, normal)

    def test_unknown_priority_falls_back_to_normal(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(sla_due_at("whatever", now), sla_due_at("normal", now))


if __name__ == "__main__":
    unittest.main()
