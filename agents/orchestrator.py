import logging
from urllib.parse import urlparse

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

from lib.workflow import Run, Snapshot
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
        self.workflow = self._build_graph()

    
    # Step functions
    def _research_step(self, state: OutreachState) -> dict:
        logger.info(
            "pipeline_step_started step=research company=%s",
            state["lead"]["company"],
        )
        if self.verbose:
            print(f"  [Research] Analysing {state['lead']['company']} …")
        try:
            result = {"research_data": self.research.run(state["lead"])}
            logger.info("pipeline_step_completed step=research")
            return result
        except ResearchAgentError as exc:
            raise OrchestratorError(f"Research step failed: {exc}") from exc

    def _marketing_step(self, state: OutreachState) -> dict:
        logger.info(
            "pipeline_step_started step=marketing company=%s",
            state["lead"]["company"],
        )
        if self.verbose:
            print("  [Marketing] Building messaging strategy …")
        try:
            result = {"marketing_data": self.marketing.run(state["research_data"])}
            logger.info("pipeline_step_completed step=marketing")
            return result
        except MarketingAgentError as exc:
            raise OrchestratorError(f"Marketing step failed: {exc}") from exc

    def _sales_step(self, state: OutreachState) -> dict:
        logger.info(
            "pipeline_step_started step=sales company=%s",
            state["lead"]["company"],
        )
        if self.verbose:
            print("  [Sales] Drafting outreach content …")
        try:
            result = {
                "sales_data": self.sales.run(
                    state["research_data"],
                    state["marketing_data"],
                )
            }
            logger.info("pipeline_step_completed step=sales")
            return result
        except SalesAgentError as exc:
            raise OrchestratorError(f"Sales step failed: {exc}") from exc

    def _report_step(self, state: OutreachState) -> dict:
        logger.info(
            "pipeline_step_started step=report company=%s",
            state["lead"]["company"],
        )
        if self.verbose:
            print("  [Report] Writing lead report …")
        try:
            path = self.writer.write(state)
            result = {"report_path": path}
            logger.info("pipeline_step_completed step=report")
            return result
        except ReportWriterError as exc:
            raise OrchestratorError(f"Report step failed: {exc}") from exc

   
    # Graph builder
    def _build_graph(self):
        graph = StateGraph(OutreachState)
        graph.add_node("research_step", self._research_step)
        graph.add_node("marketing_step", self._marketing_step)
        graph.add_node("sales_step", self._sales_step)
        graph.add_node("report_step", self._report_step)

        graph.add_edge(START, "research_step")
        graph.add_edge("research_step", "marketing_step")
        graph.add_edge("marketing_step", "sales_step")
        graph.add_edge("sales_step", "report_step")
        graph.add_edge("report_step", END)

        return graph.compile()

    def _run_graph(self, initial_state: OutreachState) -> Run:
        final_state = dict(initial_state)
        snapshots: list[Snapshot] = []

        for update in self.workflow.stream(initial_state, stream_mode="updates"):
            if not isinstance(update, dict):
                continue
            for step_id, state_update in update.items():
                if not isinstance(state_update, dict):
                    raise OrchestratorError(
                        f"Graph node '{step_id}' must return a dict, "
                        f"got {type(state_update).__name__}."
                    )
                final_state.update(state_update)
                snapshots.append(
                    Snapshot(step_id=step_id, state_data=dict(state_update))
                )

        return Run(final_state=final_state, snapshots=snapshots)

    
    # Public API
    def run(self, company: str, url: str = "") -> Run:
        """Run the full pipeline for one lead.

        Args:
            company (str): company name
            url (str): company website URL

        Returns:
            Run: completed LangGraph workflow run — call .get_final_state() for results

        Raises:
            OrchestratorError: on validation or pipeline failure
        """
        _validate_lead(company, url)

        logger.info("pipeline_started company=%s", company)
        if self.verbose:
            print(f"\n→ Starting outreach pipeline for: {company}")

        initial_state: OutreachState = {
            "lead": {"company": company, "url": url},
            "research_data": {},
            "marketing_data": {},
            "sales_data": {},
            "report_path": "",
        }
        run = self._run_graph(initial_state)
        logger.info("pipeline_completed company=%s", company)
        return run
