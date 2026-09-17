import unittest
from unittest.mock import patch

from tests.external_stubs import install

install()

from agents.handoff_writer import HandoffWriter, HandoffWriterError


def _state(**overrides):
    base = {
        "run_id": "abcdef12-0000-0000-0000-000000000000",
        "message": {
            "sender": "customer@example.com",
            "subject": "Outage again",
            "body": "This is the third outage. Call me on 415-555-1234.",
            "channel": "email",
        },
        "triage": {
            "sentiment": "angry",
            "intent": "complaint",
            "urgency": "high",
            "summary": "Third outage this week.",
            "key_points": ["repeated outages"],
            "entities": {"order_id": "A-1"},
        },
        "routing": {
            "queue": "escalation",
            "priority": "urgent",
            "owner": "support_lead",
            "escalate": True,
            "reason": "angry sentiment requires immediate human handling",
        },
        "reply": {},
        "handoff": {
            "owner": "support_lead",
            "priority": "urgent",
            "summary": "Third outage this week.",
            "key_points": ["repeated outages"],
            "entities": {"order_id": "A-1"},
        },
        "report_path": "",
    }
    base.update(overrides)
    return base


class HandoffWriterTests(unittest.TestCase):
    def _write(self, state):
        captured = {}

        def fake_write_report(filename, content):
            captured["filename"] = filename
            captured["content"] = content
            return {"path": "/tmp/note.md", "bytes": 0, "status": "written"}

        with patch("agents.handoff_writer.write_report", fake_write_report):
            HandoffWriter().write(state)
        return captured

    def test_escalated_note_contains_a_handoff_and_no_draft(self):
        captured = self._write(_state())

        self.assertIn("Inbound Escalation", captured["content"])
        self.assertIn("Handoff Brief", captured["content"])
        self.assertNotIn("Reply Draft", captured["content"])
        self.assertIn("no reply was drafted", captured["content"])

    def test_reply_note_is_clearly_marked_as_a_draft(self):
        """A generated reply must never look like it was already sent."""
        captured = self._write(
            _state(
                routing={
                    "queue": "standard",
                    "priority": "normal",
                    "owner": "sales_rep",
                    "escalate": False,
                    "reason": "routine",
                },
                reply={
                    "subject": "Re: Outage",
                    "body": "Sorry about that.",
                    "tone": "apologetic",
                    "confidence": 7,
                    "open_questions": ["Which region?"],
                },
                handoff={},
            )
        )

        self.assertIn("Reply Draft", captured["content"])
        self.assertIn("DRAFT", captured["content"])
        self.assertIn("Which region?", captured["content"])

    def test_contact_details_are_redacted(self):
        captured = self._write(_state())

        self.assertNotIn("415-555-1234", captured["content"])
        self.assertIn("[REDACTED_PHONE]", captured["content"])

    def test_triage_and_routing_signals_appear_in_the_note(self):
        captured = self._write(_state())
        content = captured["content"]

        for expected in ("angry", "complaint", "escalation", "support_lead", "urgent"):
            with self.subTest(expected=expected):
                self.assertIn(expected, content)

    def test_filename_is_scoped_by_sender_and_run(self):
        captured = self._write(_state())

        self.assertTrue(captured["filename"].startswith("inbound_customer_example_com_"))
        self.assertIn("abcdef12", captured["filename"])

    def test_write_failure_raises(self):
        def failing_write_report(filename, content):
            return {"path": "", "bytes": 0, "status": "error: disk full"}

        with patch("agents.handoff_writer.write_report", failing_write_report):
            with self.assertRaises(HandoffWriterError):
                HandoffWriter().write(_state())


if __name__ == "__main__":
    unittest.main()
