import unittest
from unittest.mock import patch

from tests.external_stubs import install

install()

from agents import orchestrator as orchestrator_module
from agents.orchestrator import Orchestrator, OrchestratorError, _validate_lead


class OrchestratorTests(unittest.TestCase):
    def test_validate_lead_rejects_empty_company(self):
        with self.assertRaisesRegex(OrchestratorError, "company"):
            _validate_lead("   ", "https://acme.example")

    def test_validate_lead_rejects_invalid_url(self):
        with self.assertRaisesRegex(OrchestratorError, "Invalid URL"):
            _validate_lead("Acme", "ftp://acme.example")

    def test_run_executes_pipeline_steps_in_order(self):
        events = []

        class FakeResearchAgent:
            def __init__(self, model):
                pass

            def run(self, lead):
                events.append(("research", lead.copy()))
                return {"_company": lead["company"], "profile": "Profile"}

        class FakeMarketingAgent:
            def __init__(self, model):
                pass

            def run(self, research_data):
                events.append(("marketing", research_data.copy()))
                return {"icp_fit": "9 - strong", "hook": "Specific hook"}

        class FakeSalesAgent:
            def __init__(self, model):
                pass

            def run(self, research_data, marketing_data):
                events.append(("sales", research_data.copy(), marketing_data.copy()))
                return {"email": {"subject": "Hello", "body": "Body"}}

        class FakeReportWriter:
            def write(self, state):
                events.append(("report", state["lead"].copy()))
                return "/tmp/acme_lead_report.md"

        patches = [
            patch.object(orchestrator_module, "ResearchAgent", FakeResearchAgent),
            patch.object(orchestrator_module, "MarketingAgent", FakeMarketingAgent),
            patch.object(orchestrator_module, "SalesAgent", FakeSalesAgent),
            patch.object(orchestrator_module, "ReportWriter", FakeReportWriter),
        ]

        with patches[0], patches[1], patches[2], patches[3]:
            run = Orchestrator(model="test-model", verbose=False).run(
                company="Acme",
                url="https://acme.example",
            )

        final = run.get_final_state()
        self.assertEqual(final["report_path"], "/tmp/acme_lead_report.md")
        self.assertEqual(final["research_data"]["profile"], "Profile")
        self.assertEqual(final["marketing_data"]["hook"], "Specific hook")
        self.assertEqual(final["sales_data"]["email"]["subject"], "Hello")
        self.assertEqual(
            [snapshot.step_id for snapshot in run.snapshots],
            ["research_step", "marketing_step", "sales_step", "report_step"],
        )
        self.assertEqual(
            [event[0] for event in events],
            ["research", "marketing", "sales", "report"],
        )


if __name__ == "__main__":
    unittest.main()
