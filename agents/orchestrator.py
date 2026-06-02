import logging
from urllib.parse import urlparse

from dotenv import load_dotenv

from lib.state_machine import StateMachine, Step, EntryPoint, Termination, Run
from agents.state import OutreachState
from agents.research_agent import ResearchAgent, ResearchAgentError
from agents.marketing_agent import MarketingAgent, MarketingAgentError
from agents.sales_agent import SalesAgent, SalesAgentError
from agents.report_writer import ReportWriter, ReportWriterError

load_dotenv("config.env")

logger = logging.getLogger(__name__)


class OrchestratorError(Exception):
    pass


def _validate_lead(company: str, url: str) -> None:
    if not company or not company.strip():
        raise OrchestratorError("'company' name must not be empty.")
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise OrchestratorError(f"Invalid URL: '{url}'. Must start with http:// or https://")


class Orchestrator:
    """Runs the full outreach pipeline for a single lead."""

    def __init__(self, model: str = "gpt-4o-mini", verbose: bool = True):
        self.verbose = verbose
        self.research = ResearchAgent(model=model)
        self.marketing = MarketingAgent(model=model)
        self.sales = SalesAgent(model=model)
        self.writer = ReportWriter()
        self.workflow = self._build_machine()

    
    # Step functions
    def _research_step(self, state: OutreachState) -> dict:
        if self.verbose:
            print(f"  [Research] Analysing {state['lead']['company']} …")
        try:
            return {"research_data": self.research.run(state["lead"])}
        except ResearchAgentError as exc:
            raise OrchestratorError(f"Research step failed: {exc}") from exc

    def _marketing_step(self, state: OutreachState) -> dict:
        if self.verbose:
            print("  [Marketing] Building messaging strategy …")
        try:
            return {"marketing_data": self.marketing.run(state["research_data"])}
        except MarketingAgentError as exc:
            raise OrchestratorError(f"Marketing step failed: {exc}") from exc

    def _sales_step(self, state: OutreachState) -> dict:
        if self.verbose:
            print("  [Sales] Drafting outreach content …")
        try:
            return {
                "sales_data": self.sales.run(
                    state["research_data"],
                    state["marketing_data"],
                )
            }
        except SalesAgentError as exc:
            raise OrchestratorError(f"Sales step failed: {exc}") from exc

    def _report_step(self, state: OutreachState) -> dict:
        if self.verbose:
            print("  [Report] Writing lead report …")
        try:
            path = self.writer.write(state)
            return {"report_path": path}
        except ReportWriterError as exc:
            raise OrchestratorError(f"Report step failed: {exc}") from exc

   
    # Machine builder
    def _build_machine(self) -> StateMachine:
        m: StateMachine[OutreachState] = StateMachine(OutreachState)

        ep = EntryPoint[OutreachState]()
        res = Step[OutreachState]("research_step", self._research_step)
        mkt = Step[OutreachState]("marketing_step", self._marketing_step)
        sal = Step[OutreachState]("sales_step", self._sales_step)
        rep = Step[OutreachState]("report_step", self._report_step)
        term = Termination[OutreachState]()

        m.add_steps([ep, res, mkt, sal, rep, term])
        m.connect(ep, res)
        m.connect(res, mkt)
        m.connect(mkt, sal)
        m.connect(sal, rep)
        m.connect(rep, term)

        return m

    
    # Public API
    def run(self, company: str, url: str = "") -> Run:
        """Run the full pipeline for one lead.

        Args:
            company (str): company name
            url (str): company website URL

        Returns:
            Run: completed state machine run — call .get_final_state() for results

        Raises:
            OrchestratorError: on validation or pipeline failure
        """
        _validate_lead(company, url)

        if self.verbose:
            print(f"\n→ Starting outreach pipeline for: {company}")

        initial_state: OutreachState = {
            "lead": {"company": company, "url": url},
            "research_data": {},
            "marketing_data": {},
            "sales_data": {},
            "report_path": "",
        }
        return self.workflow.run(initial_state)
