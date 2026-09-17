import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.external_stubs import install

install()

from agents import inbound_orchestrator as inbound_module
from agents.inbound_orchestrator import InboundOrchestrator, InboundOrchestratorError
from lib.persistence import RunStore

_ROUTINE = {
    "sentiment": "neutral",
    "intent": "question",
    "urgency": "normal",
    "summary": "Asks how to export data.",
    "key_points": ["wants CSV export"],
    "entities": {},
}
_ANGRY = {
    "sentiment": "angry",
    "intent": "complaint",
    "urgency": "high",
    "summary": "Third outage this week.",
    "key_points": ["repeated outages"],
    "entities": {"order_id": "A-1"},
}
_POLITE_CHURN = {
    "sentiment": "positive",
    "intent": "churn_risk",
    "urgency": "low",
    "summary": "Considering cancelling at renewal.",
    "key_points": ["evaluating competitors"],
    "entities": {},
}

_MESSAGE = {
    "sender": "customer@example.com",
    "subject": "Help",
    "body": "I need assistance with my account.",
    "channel": "email",
}


def _triage_returning(payload):
    class FakeTriageAgent:
        def __init__(self, model):
            pass

        def run(self, message):
            return dict(payload)

    return FakeTriageAgent


class _FakeReplyAgent:
    def __init__(self, model):
        pass

    def run(self, message, triage):
        return {
            "subject": "Re: Help",
            "body": "Here is how to export your data.",
            "tone": "helpful",
            "confidence": 8,
            "open_questions": [],
        }


class _FakeHandoffWriter:
    def write(self, state):
        return "/tmp/inbound_note.md"


class InboundOrchestratorTests(unittest.TestCase):
    def _patched(self, triage, replier=None, writer=None):
        return (
            patch.object(inbound_module, "TriageAgent", _triage_returning(triage)),
            patch.object(inbound_module, "ReplyAgent", replier or _FakeReplyAgent),
            patch.object(inbound_module, "HandoffWriter", writer or _FakeHandoffWriter),
        )

    def _run(self, triage, message=None, **kwargs):
        a, b, c = self._patched(triage, **kwargs)
        with a, b, c:
            return InboundOrchestrator(verbose=False).run(message or dict(_MESSAGE))

    def test_rejects_message_without_a_body(self):
        a, b, c = self._patched(_ROUTINE)
        with a, b, c:
            with self.assertRaisesRegex(InboundOrchestratorError, "body"):
                InboundOrchestrator(verbose=False).run({"sender": "x", "body": "   "})

    def test_routine_message_gets_a_reply_draft(self):
        run = self._run(_ROUTINE)
        state = run.get_final_state()

        self.assertEqual(state["routing"]["queue"], "standard")
        self.assertFalse(state["routing"]["escalate"])
        self.assertTrue(state["reply"]["body"])
        self.assertEqual(state["handoff"], {})

    def test_escalated_message_gets_a_handoff_and_no_draft(self):
        """Auto-drafting a reply to an angry customer invites a one-click send,
        which is exactly the wrong affordance."""
        run = self._run(_ANGRY)
        state = run.get_final_state()

        self.assertTrue(state["routing"]["escalate"])
        self.assertEqual(state["reply"], {})
        self.assertTrue(state["handoff"]["summary"])
        self.assertEqual(state["handoff"]["owner"], "support_lead")

    def test_polite_cancellation_still_escalates(self):
        run = self._run(_POLITE_CHURN)
        state = run.get_final_state()

        self.assertTrue(state["routing"]["escalate"])
        self.assertEqual(state["reply"], {})

    def test_reply_path_visits_the_expected_nodes(self):
        run = self._run(_ROUTINE)
        self.assertEqual(
            [s.step_id for s in run.snapshots],
            ["triage_step", "route_step", "reply_step", "write_step"],
        )

    def test_escalation_path_visits_the_expected_nodes(self):
        run = self._run(_ANGRY)
        self.assertEqual(
            [s.step_id for s in run.snapshots],
            ["triage_step", "route_step", "handoff_step", "write_step"],
        )

    def test_control_characters_in_the_message_are_stripped(self):
        captured = {}

        class CapturingTriage:
            def __init__(self, model):
                pass

            def run(self, message):
                captured.update(message)
                return dict(_ROUTINE)

        with patch.object(inbound_module, "TriageAgent", CapturingTriage), \
             patch.object(inbound_module, "ReplyAgent", _FakeReplyAgent), \
             patch.object(inbound_module, "HandoffWriter", _FakeHandoffWriter):
            InboundOrchestrator(verbose=False).run(
                {"sender": "a@b.com", "subject": "hi\x00", "body": "help\x1bme"}
            )

        self.assertNotIn("\x00", captured["subject"])
        self.assertNotIn("\x1b", captured["body"])

    def test_triage_failure_surfaces_as_orchestrator_error(self):
        class AlwaysFailingTriage:
            def __init__(self, model):
                pass

            def run(self, message):
                raise inbound_module.TriageAgentError("model unavailable")

        with patch.object(inbound_module, "TriageAgent", AlwaysFailingTriage), \
             patch.object(inbound_module, "ReplyAgent", _FakeReplyAgent), \
             patch.object(inbound_module, "HandoffWriter", _FakeHandoffWriter), \
             patch.object(inbound_module.time, "sleep", lambda s: None):
            with self.assertRaisesRegex(InboundOrchestratorError, "Triage step failed"):
                InboundOrchestrator(verbose=False).run(dict(_MESSAGE))


class InboundPersistenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = RunStore(Path(self._tmp.name) / "runs.db")
        self.addCleanup(self.store.close)

    def _run(self, triage):
        with patch.object(inbound_module, "TriageAgent", _triage_returning(triage)), \
             patch.object(inbound_module, "ReplyAgent", _FakeReplyAgent), \
             patch.object(inbound_module, "HandoffWriter", _FakeHandoffWriter):
            return InboundOrchestrator(verbose=False, store=self.store).run(dict(_MESSAGE))

    def test_inbound_runs_are_tagged_as_inbound(self):
        """Direction is what keeps inbound and outbound KPIs separable."""
        run = self._run(_ROUTINE)
        self.assertEqual(self.store.get_run(run.run_id)["direction"], "inbound")

    def test_routing_is_persisted_for_reporting(self):
        run = self._run(_ANGRY)
        row = self.store.get_run(run.run_id)

        self.assertEqual(row["queue"], "escalation")
        self.assertEqual(row["priority"], "urgent")
        self.assertEqual(row["owner"], "support_lead")
        self.assertEqual(row["sentiment"], "angry")
        self.assertEqual(row["escalations"], 1)

    def test_non_escalated_run_records_no_escalation(self):
        run = self._run(_ROUTINE)
        self.assertEqual(self.store.get_run(run.run_id)["escalations"], 0)

    def test_snapshots_are_persisted(self):
        run = self._run(_ROUTINE)
        steps = [s["step_id"] for s in self.store.get_snapshots(run.run_id)]
        self.assertEqual(steps, ["triage_step", "route_step", "reply_step", "write_step"])


if __name__ == "__main__":
    unittest.main()
