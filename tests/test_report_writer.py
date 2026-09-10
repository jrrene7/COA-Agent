import unittest
from unittest.mock import patch

from agents.report_writer import ReportWriter


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


if __name__ == "__main__":
    unittest.main()
