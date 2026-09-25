"""KPI tracking for pipeline runs.

The active recorder lives in a ContextVar so deep call sites (notably
`LLM.invoke`) can report token usage without every agent constructor having to
accept a run_id. Outside an active run every record_* call is a no-op, which
keeps agents independently testable.
"""

import contextvars
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


class BudgetExceededError(Exception):
    """Raised when a run has spent its token budget.

    Deliberately not an agent error: it must not be swallowed by a step retry,
    because retrying is exactly what would spend more.
    """

# USD per 1M tokens. Approximate and for relative tracking only — update when
# pricing changes. Unknown models cost 0 rather than guessing.
_PRICING_PER_MTOK = {
    "gpt-4o-mini": {"prompt": 0.15, "completion": 0.60},
    "gpt-4o": {"prompt": 2.50, "completion": 10.00},
}


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    rates = _PRICING_PER_MTOK.get(model)
    if not rates:
        return 0.0
    return (
        prompt_tokens * rates["prompt"] + completion_tokens * rates["completion"]
    ) / 1_000_000


@dataclass
class RunKpis:
    """Counters accumulated over one pipeline run."""

    run_id: str
    company: str = ""
    started_at: float = field(default_factory=time.monotonic)
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    step_retries: int = 0
    feedback_loops: int = 0
    escalations: int = 0
    token_budget: int = 0        # 0 disables the cap
    sla_due_at: str = ""
    sentiment: str = ""
    queue: str = ""
    priority: str = ""
    owner: str = ""
    step_durations: dict = field(default_factory=dict)

    def record_llm_usage(
        self, model: str, prompt_tokens: int, completion_tokens: int, total_tokens: int
    ) -> None:
        self.llm_calls += 1
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.total_tokens += total_tokens
        self.estimated_cost_usd += estimate_cost_usd(
            model, prompt_tokens, completion_tokens
        )

    def record_step_duration(self, step: str, seconds: float) -> None:
        self.step_durations[step] = self.step_durations.get(step, 0.0) + seconds

    def record_retry(self, step: str) -> None:
        self.step_retries += 1

    def record_feedback_loop(self) -> None:
        self.feedback_loops += 1

    def record_escalation(self) -> None:
        self.escalations += 1

    def record_routing(
        self,
        queue: str,
        sentiment: str = "",
        priority: str = "",
        owner: str = "",
        sla_due_at: str = "",
    ) -> None:
        self.queue = queue
        self.priority = priority or self.priority
        self.owner = owner or self.owner
        self.sla_due_at = sla_due_at or self.sla_due_at
        if sentiment:
            self.sentiment = sentiment

    def check_budget(self) -> None:
        if self.token_budget and self.total_tokens >= self.token_budget:
            raise BudgetExceededError(
                f"run {self.run_id} spent {self.total_tokens} tokens, "
                f"budget {self.token_budget}"
            )

    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    def totals(self) -> dict:
        return {
            "llm_calls": self.llm_calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
            "step_retries": self.step_retries,
            "feedback_loops": self.feedback_loops,
            "escalations": self.escalations,
            "token_budget": self.token_budget,
            "sentiment": self.sentiment,
            "queue": self.queue,
            "priority": self.priority,
            "owner": self.owner,
            "sla_due_at": self.sla_due_at,
        }


_active: contextvars.ContextVar[Optional[RunKpis]] = contextvars.ContextVar(
    "active_run_kpis", default=None
)


def current() -> Optional[RunKpis]:
    return _active.get()


def default_token_budget() -> int:
    """Per-run token ceiling. 0 (the default) disables the cap."""
    try:
        return max(0, int(os.getenv("COA_TOKEN_BUDGET", "0")))
    except ValueError:
        logger.warning("COA_TOKEN_BUDGET is not an integer; running uncapped.")
        return 0


@contextmanager
def track_run(run_id: str, company: str = "", token_budget: int | None = None):
    """Make a fresh RunKpis the active recorder for the duration of the block."""
    kpis = RunKpis(
        run_id=run_id,
        company=company,
        token_budget=default_token_budget() if token_budget is None else token_budget,
    )
    token = _active.set(kpis)
    try:
        yield kpis
    finally:
        _active.reset(token)


def record_llm_usage(
    model: str, prompt_tokens: int, completion_tokens: int, total_tokens: int
) -> None:
    kpis = _active.get()
    if kpis is not None:
        kpis.record_llm_usage(model, prompt_tokens, completion_tokens, total_tokens)


def record_retry(step: str) -> None:
    kpis = _active.get()
    if kpis is not None:
        kpis.record_retry(step)


def record_feedback_loop() -> None:
    kpis = _active.get()
    if kpis is not None:
        kpis.record_feedback_loop()


def record_escalation() -> None:
    kpis = _active.get()
    if kpis is not None:
        kpis.record_escalation()


def record_routing(
    queue: str,
    sentiment: str = "",
    priority: str = "",
    owner: str = "",
    sla_due_at: str = "",
) -> None:
    kpis = _active.get()
    if kpis is not None:
        kpis.record_routing(queue, sentiment, priority, owner, sla_due_at)


def check_budget() -> None:
    """No-op outside an active run, so agents stay independently testable."""
    kpis = _active.get()
    if kpis is not None:
        kpis.check_budget()


def record_step_duration(step: str, seconds: float) -> None:
    kpis = _active.get()
    if kpis is not None:
        kpis.record_step_duration(step, seconds)


def summarize(runs: list[dict]) -> dict:
    """Aggregate KPIs across stored run rows (as returned by RunStore)."""
    if not runs:
        return {
            "runs": 0,
            "completed": 0,
            "incomplete": 0,
            "failed": 0,
            "success_rate": 0.0,
            "feedback_loop_rate": 0.0,
            "escalation_rate": 0.0,
            "total_tokens": 0,
            "estimated_cost_usd": 0.0,
            "avg_duration_seconds": 0.0,
            "avg_tokens_per_run": 0.0,
            "by_direction": {},
            "by_queue": {},
            "by_sentiment": {},
        }

    total = len(runs)
    completed = sum(1 for r in runs if r.get("status") == "completed")
    incomplete = sum(1 for r in runs if r.get("status") == "incomplete")
    failed = sum(1 for r in runs if r.get("status") == "failed")
    looped = sum(1 for r in runs if (r.get("feedback_loops") or 0) > 0)
    tokens = sum(r.get("total_tokens") or 0 for r in runs)
    cost = sum(r.get("estimated_cost_usd") or 0.0 for r in runs)
    durations = [r["duration_seconds"] for r in runs if r.get("duration_seconds")]
    escalated = sum(1 for r in runs if (r.get("escalations") or 0) > 0)

    def _tally(field_name: str) -> dict:
        counts: dict = {}
        for row in runs:
            value = row.get(field_name)
            if value:
                counts[value] = counts.get(value, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: -kv[1]))

    return {
        "runs": total,
        "completed": completed,
        "incomplete": incomplete,
        "failed": failed,
        "success_rate": round(completed / total, 4),
        "feedback_loop_rate": round(looped / total, 4),
        "escalation_rate": round(escalated / total, 4),
        "total_tokens": tokens,
        "estimated_cost_usd": round(cost, 6),
        "avg_duration_seconds": round(sum(durations) / len(durations), 3) if durations else 0.0,
        "avg_tokens_per_run": round(tokens / total, 1),
        "by_direction": _tally("direction"),
        "by_queue": _tally("queue"),
        "by_sentiment": _tally("sentiment"),
    }
