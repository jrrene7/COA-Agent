import unittest
from unittest.mock import patch

from agents.report_writer import ReportWriter, ReportWriterError


class ReportWriterTests(unittest.TestCase):
    def test_write_builds_expected_markdown_report(self):
        calls = []

        def fake_write_report(filename, content):
            calls.append({"filename": filename, "content": content})
            return {
                "path": "/tmp/acme_corp_lead_report.md",
                "bytes": len(content.encode("utf-8")),
                "status": "written",
            }

        state = {
            "lead": {"company": "Acme Corp", "url": "https://acme.example"},
            "research_data": {
                "profile": "Acme builds workflow software.",
                "news": ["Launched a new product"],
                "people": ["Jane CEO"],
                "tech_stack": ["Python"],
                "pain_points": ["Manual reporting"],
            },
            "marketing_data": {
                "icp_fit": "8 - strong fit",
                "tone": "consultative",
                "hook": "Your new product launch is a timely moment.",
                "value_props": ["Reduce manual reporting"],
                "competitors": ["Competitor A"],
                "positioning": "Lead with speed and clarity.",
            },
            "sales_data": {
                "email": {"subject": "Reporting clarity", "body": "Short body"},
                "linkedin": "Congrats on the launch.",
                "followups": ["First follow-up"],
                "objections": [
                    {"objection": "No budget", "response": "Start small."}
                ],
                "next_action": "Ask for a 15-minute call.",
            },
            "report_path": "",
        }

        with patch("agents.report_writer.write_report", fake_write_report):
            path = ReportWriter().write(state)

        self.assertEqual(path, "/tmp/acme_corp_lead_report.md")
        self.assertEqual(calls[0]["filename"], "acme_corp_lead_report.md")
        content = calls[0]["content"]
        self.assertIn("# Lead Report: Acme Corp", content)
        self.assertIn("## 1. Company Profile", content)
        self.assertIn("## 2. Marketing Strategy", content)
        self.assertIn("## 3. Sales Outreach", content)
        self.assertIn("## 4. Objection Handling", content)
        self.assertIn("## 5. Recommended Next Action", content)
        self.assertIn("- Launched a new product", content)
        self.assertIn("1. Reduce manual reporting", content)

    def test_write_redacts_contact_details_but_keeps_people(self):
        calls = []

        def fake_write_report(filename, content):
            calls.append(content)
            return {"path": "/tmp/acme_lead_report.md", "bytes": 0, "status": "written"}

        state = {
            "lead": {"company": "Acme", "url": "https://acme.example"},
            "research_data": {
                "profile": "Reach the team at hello@acme.example or 415-555-1234.",
                "people": ["Jane Doe, CEO"],
            },
            "marketing_data": {},
            "sales_data": {},
            "report_path": "",
        }

        with patch("agents.report_writer.write_report", fake_write_report):
            ReportWriter().write(state)

        content = calls[0]
        self.assertNotIn("hello@acme.example", content)
        self.assertNotIn("415-555-1234", content)
        self.assertIn("[REDACTED_EMAIL]", content)
        self.assertIn("[REDACTED_PHONE]", content)
        # Decision-maker names and titles are the report's purpose.
        self.assertIn("Jane Doe, CEO", content)

    def test_write_raises_when_underlying_write_fails(self):
        def failing_write_report(filename, content):
            return {"path": "", "bytes": 0, "status": "error: disk full"}

        state = {
            "lead": {"company": "Acme", "url": ""},
            "research_data": {},
            "marketing_data": {},
            "sales_data": {},
            "report_path": "",
        }

        with patch("agents.report_writer.write_report", failing_write_report):
            with self.assertRaises(ReportWriterError):
                ReportWriter().write(state)



class ReportWriterReviewTests(unittest.TestCase):
    """The report is the surface a human actually opens, so the review verdict
    has to be visible there — not only in state and the run store."""

    def _write(self, evaluation, routing):
        captured = {}

        def fake_write_report(filename, content):
            captured["content"] = content
            return {"path": "/tmp/acme.md", "bytes": 0, "status": "written"}

        state = {
            "lead": {"company": "Acme", "url": "https://acme.example"},
            "research_data": {"profile": "Acme builds workflow software."},
            "marketing_data": {"icp_fit": "9"},
            "sales_data": {"email": {"subject": "S", "body": "B"}},
            "evaluation": evaluation,
            "routing": routing,
            "report_path": "",
        }
        with patch("agents.report_writer.write_report", fake_write_report):
            ReportWriter().write(state)
        return captured["content"]

    def test_escalated_report_warns_before_the_draft(self):
        content = self._write(
            {"overall": 5, "verdict": "revise", "sentiment": "neutral"},
            {
                "queue": "human_review",
                "priority": "normal",
                "owner": "sales_manager",
                "escalate": True,
                "reason": "below approve threshold",
            },
        )

        self.assertIn("NEEDS HUMAN REVIEW", content)
        self.assertIn("DO NOT SEND AS-IS", content)
        # The warning must precede the draft, not trail it.
        self.assertLess(content.index("NEEDS HUMAN REVIEW"), content.index("## 3. Sales Outreach"))

    def test_approved_report_is_not_marked_for_review(self):
        content = self._write(
            {"overall": 9, "verdict": "approve", "sentiment": "positive"},
            {
                "queue": "priority",
                "priority": "high",
                "owner": "senior_rep",
                "escalate": False,
                "reason": "approved draft (9.0)",
            },
        )

        self.assertNotIn("NEEDS HUMAN REVIEW", content)
        self.assertIn("Approved for outreach", content)

    def test_component_scores_and_issues_are_shown(self):
        content = self._write(
            {
                "overall": 5,
                "verdict": "revise",
                "sentiment": "neutral",
                "scores": {"personalisation": 3, "cta_strength": 4},
                "issues": ["Hook is generic."],
                "critique": "Anchor the opening to the Series C.",
            },
            {"queue": "human_review", "owner": "sales_manager", "escalate": True, "reason": "x"},
        )

        self.assertIn("Personalisation | 3/10", content)
        self.assertIn("Hook is generic.", content)
        self.assertIn("Anchor the opening to the Series C.", content)

    def test_routing_details_are_recorded_in_the_report(self):
        content = self._write(
            {"overall": 9, "verdict": "approve"},
            {
                "queue": "priority",
                "priority": "high",
                "owner": "senior_rep",
                "escalate": False,
                "reason": "strong-fit lead",
            },
        )

        self.assertIn("senior_rep", content)
        self.assertIn("strong-fit lead", content)

    def test_unreviewed_report_says_so_rather_than_implying_approval(self):
        content = self._write({}, {})

        self.assertIn("not reviewed", content)
        self.assertNotIn("Approved for outreach", content)

if __name__ == "__main__":
    unittest.main()
