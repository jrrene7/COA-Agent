"""Scoring logic for evaluator evals.

Deliberately pure — no API calls, no I/O — so the scoring itself is unit tested
offline while `run.py` supplies the live evaluator output.

The gate under test is the *whole* decision path, not just the model: a case
passes only if `route_outbound` reaches the right send/review outcome. That way
the evals also cover the threshold constants, which are the part most likely to
be mistuned.
"""

from dataclasses import dataclass, field
from typing import Optional

from evals.cases import LABEL_REVIEW, LABEL_SEND, EvalCase
from lib.routing import route_outbound

# A single approved bad draft is a real email to a real prospect, so the default
# bar for that direction is zero. Over-flagging only costs reviewer minutes.
DEFAULT_MAX_FALSE_APPROVALS = 0
DEFAULT_MIN_AGREEMENT = 0.75


@dataclass
class CaseResult:
    case_id: str
    expected: str
    actual: str
    overall: Optional[float]
    verdict: str
    sentiment: str
    queue: str
    escalated: bool
    error: str = ""

    @property
    def agreed(self) -> bool:
        return not self.error and self.expected == self.actual

    @property
    def is_false_approval(self) -> bool:
        """Labelled review, but the pipeline would have sent it."""
        return not self.error and self.expected == LABEL_REVIEW and self.actual == LABEL_SEND

    @property
    def is_false_review(self) -> bool:
        """Labelled send, but the pipeline flagged it for a human."""
        return not self.error and self.expected == LABEL_SEND and self.actual == LABEL_REVIEW


def decide(evaluation: dict) -> str:
    """Run an evaluation through the real routing gate and reduce to send/review."""
    decision = route_outbound(evaluation, marketing_data={}, attempts_exhausted=False)
    return LABEL_REVIEW if decision.escalate else LABEL_SEND


def score_case(case: EvalCase, evaluation: dict, error: str = "") -> CaseResult:
    if error:
        return CaseResult(
            case_id=case.case_id,
            expected=case.label,
            actual="",
            overall=None,
            verdict="",
            sentiment="",
            queue="",
            escalated=False,
            error=error,
        )

    decision = route_outbound(evaluation, marketing_data={}, attempts_exhausted=False)
    overall = evaluation.get("overall")
    return CaseResult(
        case_id=case.case_id,
        expected=case.label,
        actual=LABEL_REVIEW if decision.escalate else LABEL_SEND,
        overall=float(overall) if isinstance(overall, (int, float)) else None,
        verdict=str(evaluation.get("verdict", "")),
        sentiment=str(evaluation.get("sentiment", "")),
        queue=decision.queue,
        escalated=decision.escalate,
    )


@dataclass
class EvalSummary:
    total: int
    scored: int
    errors: int
    agreed: int
    false_approvals: int
    false_reviews: int
    agreement: float
    score_ranges: dict = field(default_factory=dict)
    results: list = field(default_factory=list)

    def passed(
        self,
        max_false_approvals: int = DEFAULT_MAX_FALSE_APPROVALS,
        min_agreement: float = DEFAULT_MIN_AGREEMENT,
    ) -> bool:
        if self.errors:
            return False
        return (
            self.false_approvals <= max_false_approvals
            and self.agreement >= min_agreement
        )


def summarize(results: list) -> EvalSummary:
    total = len(results)
    errors = sum(1 for r in results if r.error)
    scored = total - errors
    agreed = sum(1 for r in results if r.agreed)

    score_ranges = {}
    for label in (LABEL_SEND, LABEL_REVIEW):
        scores = [r.overall for r in results if r.expected == label and r.overall is not None]
        if scores:
            score_ranges[label] = {
                "min": min(scores),
                "max": max(scores),
                "mean": round(sum(scores) / len(scores), 2),
                "n": len(scores),
            }

    return EvalSummary(
        total=total,
        scored=scored,
        errors=errors,
        agreed=agreed,
        false_approvals=sum(1 for r in results if r.is_false_approval),
        false_reviews=sum(1 for r in results if r.is_false_review),
        agreement=round(agreed / scored, 4) if scored else 0.0,
        score_ranges=score_ranges,
        results=results,
    )


def threshold_advice(summary: EvalSummary) -> str:
    """Suggest whether the approve threshold sits between the two populations.

    The useful signal is separation: if the worst 'send' draft scores below the
    best 'review' draft, no threshold can split them and the problem is the
    model's scoring, not the constant.
    """
    send = summary.score_ranges.get(LABEL_SEND)
    review = summary.score_ranges.get(LABEL_REVIEW)
    if not send or not review:
        return "Not enough scored cases in both classes to advise on thresholds."

    if send["min"] > review["max"]:
        low, high = review["max"], send["min"]
        return (
            f"Clean separation: 'review' tops out at {low:.0f} and 'send' bottoms "
            f"out at {high:.0f}. Any approve threshold in ({low:.0f}, {high:.0f}] "
            f"separates them."
        )
    return (
        f"Overlap: 'send' drafts go as low as {send['min']} while 'review' drafts "
        f"reach {review['max']}. No single threshold separates these populations — "
        f"the evaluator's scoring needs work before the constant is worth tuning."
    )


def format_report(summary: EvalSummary) -> str:
    lines = ["", "=" * 68, "EvaluatorAgent eval results", "=" * 68, ""]

    for result in summary.results:
        if result.error:
            lines.append(f"  ERROR   {result.case_id}: {result.error}")
            continue
        if result.agreed:
            mark = "  ok    "
        elif result.is_false_approval:
            mark = "  FALSE-APPROVE "
        else:
            mark = "  flagged"
        score = f"{result.overall:.0f}" if result.overall is not None else "?"
        lines.append(
            f"{mark} {result.case_id:<28} expected={result.expected:<6} "
            f"got={result.actual or '-':<6} score={score:<3} verdict={result.verdict}"
        )

    lines += ["", "-" * 68]
    lines.append(f"  cases scored      : {summary.scored}/{summary.total}")
    lines.append(f"  agreement         : {summary.agreement:.0%}")
    lines.append(f"  false approvals   : {summary.false_approvals}   (bad draft would have been sent)")
    lines.append(f"  false reviews     : {summary.false_reviews}   (good draft sent to a human)")
    if summary.errors:
        lines.append(f"  errors            : {summary.errors}")

    for label, stats in summary.score_ranges.items():
        lines.append(
            f"  scores [{label:<6}]   : min={stats['min']:.0f} "
            f"mean={stats['mean']} max={stats['max']:.0f} (n={stats['n']})"
        )

    lines += ["", f"  {threshold_advice(summary)}", "-" * 68, ""]
    return "\n".join(lines)
