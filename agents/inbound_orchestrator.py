import logging
import time
import uuid

from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph

from lib import kpi
from lib.persistence import (
    DIRECTION_INBOUND,
    STATUS_COMPLETED,
    STATUS_FAILED,
    RunStore,
)
from lib.routing import route_inbound
from lib.security import sanitize_text
from lib.workflow import Run, Snapshot
from agents.state import InboundState
from agents.triage_agent import TriageAgent, TriageAgentError
from agents.reply_agent import ReplyAgent, ReplyAgentError
from agents.handoff_writer import HandoffWriter, HandoffWriterError

load_dotenv("config.env")

logger = logging.getLogger(__name__)


class InboundOrchestratorError(Exception):
    pass


_STEP_MAX_ATTEMPTS = 3
_STEP_RETRY_BACKOFF = 1.5


def _run_with_retry(fn, *args, retry_on, step_name: str, **kwargs):
    """Same bounded retry contract as the outbound pipeline."""
    last_exc = None
    for attempt in range(1, _STEP_MAX_ATTEMPTS + 1):
        try:
            return fn(*args, **kwargs)
        except retry_on as exc:
            last_exc = exc
            kpi.record_retry(step_name)
            if attempt < _STEP_MAX_ATTEMPTS:
                wait = _STEP_RETRY_BACKOFF ** attempt
                logger.warning(
                    "inbound_step_retry step=%s attempt=%d/%d error=%s wait=%.1fs",
                    step_name,
                    attempt,
                    _STEP_MAX_ATTEMPTS,
                    exc,
                    wait,
                )
                time.sleep(wait)
    raise last_exc


def _validate_message(message: dict) -> None:
    if not message or not str(message.get("body", "")).strip():
        raise InboundOrchestratorError("Inbound message must include a non-empty 'body'.")


class InboundOrchestrator:
    """Triages one inbound customer message, routes it, and drafts a response.

    Mirrors the outbound Orchestrator's structure: each graph node wraps a pure
    agent with logging, bounded retry, and error translation.
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        verbose: bool = True,
        store: "RunStore | None" = None,
    ):
        self.verbose = verbose
        self.store = store
        self.triage = TriageAgent(model=model)
        self.replier = ReplyAgent(model=model)
        self.writer = HandoffWriter()
        self.workflow = self._build_graph()

    def _finish_step(self, run_id: str, step: str, started: float, status: str) -> None:
        elapsed = time.monotonic() - started
        kpi.record_step_duration(step, elapsed)
        if self.store is not None:
            self.store.record_step_metric(run_id, step, status, elapsed)

    # Step functions
    def _triage_step(self, state: InboundState) -> dict:
        logger.info("inbound_step_started run_id=%s step=triage", state["run_id"])
        if self.verbose:
            print("  [Triage] Classifying inbound message …")
        started = time.monotonic()
        try:
            data = _run_with_retry(
                self.triage.run,
                state["message"],
                retry_on=(TriageAgentError,),
                step_name="triage",
            )
            self._finish_step(state["run_id"], "triage", started, "completed")
            logger.info(
                "inbound_step_completed run_id=%s step=triage sentiment=%s "
                "intent=%s urgency=%s",
                state["run_id"],
                data.get("sentiment"),
                data.get("intent"),
                data.get("urgency"),
            )
            return {"triage": data}
        except TriageAgentError as exc:
            self._finish_step(state["run_id"], "triage", started, "failed")
            raise InboundOrchestratorError(f"Triage step failed: {exc}") from exc

    def _route_step(self, state: InboundState) -> dict:
        decision = route_inbound(state.get("triage") or {})
        triage = state.get("triage") or {}

        if decision.escalate:
            kpi.record_escalation()
            logger.warning(
                "inbound_escalated run_id=%s queue=%s owner=%s priority=%s reason=%s",
                state["run_id"],
                decision.queue,
                decision.owner,
                decision.priority,
                decision.reason,
            )
        kpi.record_routing(
            decision.queue,
            str(triage.get("sentiment", "")),
            decision.priority,
            decision.owner,
        )
        logger.info(
            "inbound_routed run_id=%s queue=%s priority=%s owner=%s escalate=%s",
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

    def _route_after_triage(self, state: InboundState) -> str:
        """Escalated messages skip the draft entirely.

        Auto-drafting a reply to someone who is angry or cancelling invites a
        human to send it with one click, which is exactly the wrong affordance.
        They get a handoff brief instead.
        """
        if (state.get("routing") or {}).get("escalate"):
            return "handoff_step"
        return "reply_step"

    def _reply_step(self, state: InboundState) -> dict:
        logger.info("inbound_step_started run_id=%s step=reply", state["run_id"])
        if self.verbose:
            print("  [Reply] Drafting response …")
        started = time.monotonic()
        try:
            data = _run_with_retry(
                self.replier.run,
                state["message"],
                state["triage"],
                retry_on=(ReplyAgentError,),
                step_name="reply",
            )
            self._finish_step(state["run_id"], "reply", started, "completed")
            logger.info(
                "inbound_step_completed run_id=%s step=reply confidence=%s",
                state["run_id"],
                data.get("confidence"),
            )
            return {"reply": data}
        except ReplyAgentError as exc:
            self._finish_step(state["run_id"], "reply", started, "failed")
            raise InboundOrchestratorError(f"Reply step failed: {exc}") from exc

    def _handoff_step(self, state: InboundState) -> dict:
        """Assemble the human handoff brief.

        Deterministic on purpose: a handoff is a factual summary of what triage
        already determined, so there is nothing for a second model call to add
        except a chance to hallucinate.
        """
        triage = state.get("triage") or {}
        routing = state.get("routing") or {}
        message = state.get("message") or {}

        handoff = {
            "reason": routing.get("reason", ""),
            "owner": routing.get("owner", ""),
            "priority": routing.get("priority", ""),
            "sentiment": triage.get("sentiment", ""),
            "intent": triage.get("intent", ""),
            "summary": triage.get("summary", ""),
            "key_points": triage.get("key_points", []),
            "entities": triage.get("entities", {}),
            "sender": message.get("sender", ""),
        }
        logger.info(
            "inbound_handoff_prepared run_id=%s owner=%s intent=%s",
            state["run_id"],
            handoff["owner"],
            handoff["intent"],
        )
        if self.verbose:
            print(f"  [Handoff] Prepared brief for {handoff['owner']} …")
        return {"handoff": handoff}

    def _write_step(self, state: InboundState) -> dict:
        logger.info("inbound_step_started run_id=%s step=write", state["run_id"])
        if self.verbose:
            print("  [Write] Writing inbound note …")
        started = time.monotonic()
        try:
            path = self.writer.write(state)
            self._finish_step(state["run_id"], "write", started, "completed")
            logger.info("inbound_step_completed run_id=%s step=write", state["run_id"])
            return {"report_path": path}
        except HandoffWriterError as exc:
            self._finish_step(state["run_id"], "write", started, "failed")
            raise InboundOrchestratorError(f"Write step failed: {exc}") from exc

    # Graph builder
    def _build_graph(self):
        graph = StateGraph(InboundState)
        graph.add_node("triage_step", self._triage_step)
        graph.add_node("route_step", self._route_step)
        graph.add_node("reply_step", self._reply_step)
        graph.add_node("handoff_step", self._handoff_step)
        graph.add_node("write_step", self._write_step)

        graph.add_edge(START, "triage_step")
        graph.add_edge("triage_step", "route_step")
        graph.add_conditional_edges(
            "route_step",
            self._route_after_triage,
            {"reply_step": "reply_step", "handoff_step": "handoff_step"},
        )
        graph.add_edge("reply_step", "write_step")
        graph.add_edge("handoff_step", "write_step")
        graph.add_edge("write_step", END)

        return graph.compile()

    def _run_graph(self, initial_state: InboundState) -> Run:
        final_state = dict(initial_state)
        snapshots: list[Snapshot] = []
        run_id = initial_state["run_id"]

        for update in self.workflow.stream(initial_state, stream_mode="updates"):
            if not isinstance(update, dict):
                continue
            for step_id, state_update in update.items():
                if not isinstance(state_update, dict):
                    raise InboundOrchestratorError(
                        f"Graph node '{step_id}' must return a dict, "
                        f"got {type(state_update).__name__}."
                    )
                final_state.update(state_update)
                snapshots.append(Snapshot(step_id=step_id, state_data=dict(state_update)))
                if self.store is not None:
                    self.store.record_snapshot(
                        run_id, len(snapshots), step_id, dict(state_update)
                    )

        return Run(final_state=final_state, snapshots=snapshots)

    # Public API
    def run(self, message: dict) -> Run:
        """Triage, route, and draft a response for one inbound message.

        Args:
            message (dict): {"sender", "subject", "body", "channel"}

        Returns:
            Run: completed workflow run — call .get_final_state() for results

        Raises:
            InboundOrchestratorError: on validation or pipeline failure
        """
        message = {
            "sender": sanitize_text(str(message.get("sender", ""))).strip(),
            "subject": sanitize_text(str(message.get("subject", ""))).strip(),
            "body": sanitize_text(str(message.get("body", ""))).strip(),
            "channel": sanitize_text(str(message.get("channel", "email"))).strip(),
        }
        _validate_message(message)

        run_id = str(uuid.uuid4())
        sender = message["sender"] or "unknown"
        logger.info("inbound_started run_id=%s sender=%s", run_id, sender)
        if self.verbose:
            print(f"\n→ Triaging inbound message from: {sender} (run_id={run_id})")

        initial_state: InboundState = {
            "run_id": run_id,
            "message": message,
            "triage": {},
            "routing": {},
            "reply": {},
            "handoff": {},
            "report_path": "",
        }

        if self.store is not None:
            self.store.start_run(run_id, sender, "", direction=DIRECTION_INBOUND)

        with kpi.track_run(run_id, sender) as kpis:
            try:
                run = self._run_graph(initial_state)
            except Exception as exc:
                self._finish_run(run_id, STATUS_FAILED, kpis, error=str(exc))
                raise
            report_path = run.get_final_state().get("report_path", "")
            self._finish_run(run_id, STATUS_COMPLETED, kpis, report_path=report_path)

        logger.info(
            "inbound_completed run_id=%s sender=%s total_tokens=%d",
            run_id,
            sender,
            kpis.total_tokens,
        )
        return run

    def _finish_run(
        self, run_id: str, status: str, kpis, report_path: str = "", error: str = ""
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
