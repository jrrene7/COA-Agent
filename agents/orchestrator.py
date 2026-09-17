import logging
import time
import uuid
from typing import Callable, Tuple, Type
from urllib.parse import urlparse

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

from lib import kpi
from lib.persistence import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_INCOMPLETE,
    RunStore,
)
from lib.routing import route_outbound
from lib.security import sanitize_text
from lib.workflow import Run, Snapshot
from agents.state import OutreachState
from agents.research_agent import ResearchAgent, ResearchAgentError
from agents.marketing_agent import MarketingAgent, MarketingAgentError
from agents.sales_agent import SalesAgent, SalesAgentError
from agents.report_writer import ReportWriter, ReportWriterError
from agents.evaluator_agent import EvaluatorAgent, EvaluatorAgentError

load_dotenv("config.env")

logger = logging.getLogger(__name__)


class OrchestratorError(Exception):
    pass


_STEP_MAX_ATTEMPTS = 3
_STEP_RETRY_BACKOFF = 1.5
_MAX_FEEDBACK_ATTEMPTS = 2


def _run_with_retry(
    fn: Callable,
    *args,
    retry_on: Tuple[Type[Exception], ...],
    step_name: str,
    **kwargs,
):
    """Call `fn`, retrying with backoff if it raises one of `retry_on`.

    Covers transient agent failures that aren't caught by the LLM client's own
    retry loop — e.g. a malformed/incomplete JSON response that fails schema
    validation but might succeed on a fresh attempt.
    """
    last_exc: Exception | None = None
    for attempt in range(1, _STEP_MAX_ATTEMPTS + 1):
        try:
            return fn(*args, **kwargs)
        except retry_on as exc:
            last_exc = exc
            kpi.record_retry(step_name)
            if attempt < _STEP_MAX_ATTEMPTS:
                wait = _STEP_RETRY_BACKOFF ** attempt
                logger.warning(
                    "pipeline_step_retry step=%s attempt=%d/%d error=%s wait=%.1fs",
                    step_name,
                    attempt,
                    _STEP_MAX_ATTEMPTS,
                    exc,
                    wait,
                )
                time.sleep(wait)
    raise last_exc


def _validate_lead(company: str, url: str) -> None:
    if not company or not company.strip():
        raise OrchestratorError("'company' name must not be empty.")
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise OrchestratorError(f"Invalid URL: '{url}'. Must start with http:// or https://")


class Orchestrator:
    """Runs the full outreach pipeline for a single lead."""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        verbose: bool = True,
        store: "RunStore | None" = None,
    ):
        self.verbose = verbose
        self.store = store
        self.research = ResearchAgent(model=model)
        self.marketing = MarketingAgent(model=model)
        self.sales = SalesAgent(model=model)
        self.evaluator = EvaluatorAgent(model=model)
        self.writer = ReportWriter()
        self.workflow = self._build_graph()

    
    def _finish_step(self, run_id: str, step: str, started: float, status: str) -> None:
        elapsed = time.monotonic() - started
        kpi.record_step_duration(step, elapsed)
        if self.store is not None:
            self.store.record_step_metric(run_id, step, status, elapsed)

    # Step functions
    def _research_step(self, state: OutreachState) -> dict:
        logger.info(
            "pipeline_step_started run_id=%s step=research company=%s",
            state["run_id"],
            state["lead"]["company"],
        )
        if self.verbose:
            print(f"  [Research] Analysing {state['lead']['company']} …")
        started = time.monotonic()
        try:
            data = _run_with_retry(
                self.research.run,
                state["lead"],
                retry_on=(ResearchAgentError,),
                step_name="research",
            )
            result = {"research_data": data}
            self._finish_step(state["run_id"], "research", started, "completed")
            logger.info("pipeline_step_completed run_id=%s step=research", state["run_id"])
            return result
        except ResearchAgentError as exc:
            self._finish_step(state["run_id"], "research", started, "failed")
            raise OrchestratorError(f"Research step failed: {exc}") from exc

    def _marketing_step(self, state: OutreachState) -> dict:
        logger.info(
            "pipeline_step_started run_id=%s step=marketing company=%s",
            state["run_id"],
            state["lead"]["company"],
        )
        if self.verbose:
            print("  [Marketing] Building messaging strategy …")
        started = time.monotonic()
        try:
            data = _run_with_retry(
                self.marketing.run,
                state["research_data"],
                state.get("critique", ""),
                retry_on=(MarketingAgentError,),
                step_name="marketing",
            )
            result = {"marketing_data": data}
            self._finish_step(state["run_id"], "marketing", started, "completed")
            logger.info("pipeline_step_completed run_id=%s step=marketing", state["run_id"])
            return result
        except MarketingAgentError as exc:
            self._finish_step(state["run_id"], "marketing", started, "failed")
            raise OrchestratorError(f"Marketing step failed: {exc}") from exc

    def _sales_step(self, state: OutreachState) -> dict:
        logger.info(
            "pipeline_step_started run_id=%s step=sales company=%s",
            state["run_id"],
            state["lead"]["company"],
        )
        if self.verbose:
            print("  [Sales] Drafting outreach content …")
        started = time.monotonic()
        try:
            data = _run_with_retry(
                self.sales.run,
                state["research_data"],
                state["marketing_data"],
                retry_on=(SalesAgentError,),
                step_name="sales",
            )
            result = {
                "sales_data": data,
                "feedback_attempts": state.get("feedback_attempts", 0) + 1,
            }
            self._finish_step(state["run_id"], "sales", started, "completed")
            logger.info("pipeline_step_completed run_id=%s step=sales", state["run_id"])
            return result
        except SalesAgentError as exc:
            self._finish_step(state["run_id"], "sales", started, "failed")
            raise OrchestratorError(f"Sales step failed: {exc}") from exc

    def _report_step(self, state: OutreachState) -> dict:
        logger.info(
            "pipeline_step_started run_id=%s step=report company=%s",
            state["run_id"],
            state["lead"]["company"],
        )
        if self.verbose:
            print("  [Report] Writing lead report …")
        started = time.monotonic()
        try:
            path = self.writer.write(state)
            result = {"report_path": path}
            self._finish_step(state["run_id"], "report", started, "completed")
            logger.info("pipeline_step_completed run_id=%s step=report", state["run_id"])
            return result
        except ReportWriterError as exc:
            self._finish_step(state["run_id"], "report", started, "failed")
            raise OrchestratorError(f"Report step failed: {exc}") from exc

   
    def _route_after_research(self, state: OutreachState) -> str:
        """Only advance to marketing once research produced a usable profile."""
        profile = (state.get("research_data") or {}).get("profile", "")
        if not profile or not str(profile).strip():
            logger.error(
                "pipeline_step_incomplete run_id=%s step=research reason=empty_profile",
                state["run_id"],
            )
            return END
        return "marketing_step"

    def _route_after_evaluation(self, state: OutreachState) -> str:
        """Quality gate: a draft the evaluator rejected goes back to marketing
        carrying the critique, so the retry is a correction rather than a re-roll
        of identical inputs. Bounded by _MAX_FEEDBACK_ATTEMPTS; once the budget is
        spent the draft is routed to a human instead of looping.
        """
        evaluation = state.get("evaluation") or {}
        verdict = str(evaluation.get("verdict", "revise")).strip().lower()
        attempts = state.get("feedback_attempts", 0)
        exhausted = attempts >= _MAX_FEEDBACK_ATTEMPTS

        if verdict == "revise" and not exhausted:
            kpi.record_feedback_loop()
            logger.warning(
                "pipeline_feedback_loop run_id=%s step=evaluate attempt=%d/%d "
                "verdict=revise overall=%s",
                state["run_id"],
                attempts,
                _MAX_FEEDBACK_ATTEMPTS,
                evaluation.get("overall"),
            )
            return "marketing_step"

        return "route_step"

    def _evaluate_step(self, state: OutreachState) -> dict:
        logger.info(
            "pipeline_step_started run_id=%s step=evaluate company=%s",
            state["run_id"],
            state["lead"]["company"],
        )
        if self.verbose:
            print("  [Evaluate] Reviewing draft quality …")
        started = time.monotonic()
        try:
            data = _run_with_retry(
                self.evaluator.run,
                state["research_data"],
                state["marketing_data"],
                state["sales_data"],
                retry_on=(EvaluatorAgentError,),
                step_name="evaluate",
            )
            result = {
                "evaluation": data,
                "critique": str(data.get("critique", "")),
            }
            self._finish_step(state["run_id"], "evaluate", started, "completed")
            logger.info(
                "pipeline_step_completed run_id=%s step=evaluate verdict=%s "
                "overall=%s sentiment=%s",
                state["run_id"],
                data.get("verdict"),
                data.get("overall"),
                data.get("sentiment"),
            )
            return result
        except EvaluatorAgentError as exc:
            self._finish_step(state["run_id"], "evaluate", started, "failed")
            raise OrchestratorError(f"Evaluate step failed: {exc}") from exc

    def _route_step(self, state: OutreachState) -> dict:
        """Turn evaluator signals into an owner, a queue, and a priority."""
        evaluation = state.get("evaluation") or {}
        decision = route_outbound(
            evaluation,
            marketing_data=state.get("marketing_data") or {},
            attempts_exhausted=state.get("feedback_attempts", 0) >= _MAX_FEEDBACK_ATTEMPTS,
        )
        if decision.escalate:
            kpi.record_escalation()
            logger.warning(
                "pipeline_escalated run_id=%s queue=%s owner=%s reason=%s",
                state["run_id"],
                decision.queue,
                decision.owner,
                decision.reason,
            )
        kpi.record_routing(
            decision.queue,
            str(evaluation.get("sentiment", "")),
            decision.priority,
            decision.owner,
        )
        logger.info(
            "pipeline_routed run_id=%s queue=%s priority=%s owner=%s escalate=%s",
            state["run_id"],
            decision.queue,
            decision.priority,
            decision.owner,
            decision.escalate,
        )
        if self.verbose:
            flag = " (escalated)" if decision.escalate else ""
            print(f"  [Route] → {decision.queue} / {decision.owner}{flag}")
        return {"routing": decision.to_dict()}

    # Graph builder
    def _build_graph(self):
        graph = StateGraph(OutreachState)
        graph.add_node("research_step", self._research_step)
        graph.add_node("marketing_step", self._marketing_step)
        graph.add_node("sales_step", self._sales_step)
        graph.add_node("evaluate_step", self._evaluate_step)
        graph.add_node("route_step", self._route_step)
        graph.add_node("report_step", self._report_step)

        graph.add_edge(START, "research_step")
        graph.add_conditional_edges(
            "research_step",
            self._route_after_research,
            {"marketing_step": "marketing_step", END: END},
        )
        graph.add_edge("marketing_step", "sales_step")
        graph.add_edge("sales_step", "evaluate_step")
        graph.add_conditional_edges(
            "evaluate_step",
            self._route_after_evaluation,
            {"marketing_step": "marketing_step", "route_step": "route_step"},
        )
        graph.add_edge("route_step", "report_step")
        graph.add_edge("report_step", END)

        return graph.compile()

    def _run_graph(self, initial_state: OutreachState) -> Run:
        final_state = dict(initial_state)
        snapshots: list[Snapshot] = []
        run_id = initial_state["run_id"]

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
                if self.store is not None:
                    # Written as each node lands, so a crash mid-pipeline still
                    # leaves everything completed so far recoverable by run_id.
                    self.store.record_snapshot(
                        run_id, len(snapshots), step_id, dict(state_update)
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
        company = sanitize_text(company).strip()
        url = sanitize_text(url).strip()
        _validate_lead(company, url)

        run_id = str(uuid.uuid4())
        logger.info("pipeline_started run_id=%s company=%s", run_id, company)
        if self.verbose:
            print(f"\n→ Starting outreach pipeline for: {company} (run_id={run_id})")

        initial_state: OutreachState = {
            "run_id": run_id,
            "lead": {"company": company, "url": url},
            "research_data": {},
            "marketing_data": {},
            "sales_data": {},
            "evaluation": {},
            "critique": "",
            "routing": {},
            "report_path": "",
            "feedback_attempts": 0,
        }

        if self.store is not None:
            self.store.start_run(run_id, company, url)

        with kpi.track_run(run_id, company) as kpis:
            try:
                run = self._run_graph(initial_state)
            except Exception as exc:
                self._finish_run(run_id, STATUS_FAILED, kpis, error=str(exc))
                raise

            report_path = run.get_final_state().get("report_path", "")
            status = STATUS_COMPLETED if report_path else STATUS_INCOMPLETE
            self._finish_run(run_id, status, kpis, report_path=report_path)

        logger.info(
            "pipeline_completed run_id=%s company=%s status=%s total_tokens=%d "
            "estimated_cost_usd=%.6f",
            run_id,
            company,
            status,
            kpis.total_tokens,
            kpis.estimated_cost_usd,
        )
        return run

    def _finish_run(
        self,
        run_id: str,
        status: str,
        kpis: "kpi.RunKpis",
        report_path: str = "",
        error: str = "",
    ) -> None:
        if self.store is None:
            return
        self.store.finish_run(
            run_id,
            status=status,
            report_path=report_path,
            error=error,
            duration_seconds=kpis.elapsed_seconds(),
            totals=kpis.totals(),
        )
