"""Routing and escalation rules shared by the outbound and inbound pipelines.

Agents produce *signals* (sentiment, urgency, intent, quality scores); this
module turns those signals into a decision. The split is deliberate: a model
opinion in the routing path would be a second source of non-determinism on top
of the drafting itself, and escalation decisions need to be explainable and
testable. Every decision carries the reason that produced it.
"""

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional

# Queues
QUEUE_STANDARD = "standard"
QUEUE_PRIORITY = "priority"
QUEUE_HUMAN_REVIEW = "human_review"
QUEUE_ESCALATION = "escalation"

# Priorities, ordered low to high
PRIORITY_LOW = "low"
PRIORITY_NORMAL = "normal"
PRIORITY_HIGH = "high"
PRIORITY_URGENT = "urgent"

_PRIORITY_ORDER = [PRIORITY_LOW, PRIORITY_NORMAL, PRIORITY_HIGH, PRIORITY_URGENT]

# Sentiment vocabulary
SENTIMENT_POSITIVE = "positive"
SENTIMENT_NEUTRAL = "neutral"
SENTIMENT_NEGATIVE = "negative"
SENTIMENT_ANGRY = "angry"

_SENTIMENTS = {SENTIMENT_POSITIVE, SENTIMENT_NEUTRAL, SENTIMENT_NEGATIVE, SENTIMENT_ANGRY}

# Inbound intents
INTENT_QUESTION = "question"
INTENT_INTERESTED = "interested"
INTENT_COMPLAINT = "complaint"
INTENT_CHURN_RISK = "churn_risk"
INTENT_BILLING = "billing"
INTENT_UNSUBSCRIBE = "unsubscribe"
INTENT_OTHER = "other"

_INTENTS = {
    INTENT_QUESTION,
    INTENT_INTERESTED,
    INTENT_COMPLAINT,
    INTENT_CHURN_RISK,
    INTENT_BILLING,
    INTENT_UNSUBSCRIBE,
    INTENT_OTHER,
}

# Intents a human must own regardless of tone.
_ALWAYS_ESCALATE_INTENTS = {INTENT_CHURN_RISK, INTENT_UNSUBSCRIBE}

# Owner per queue. A real deployment would resolve these against a rota.
_QUEUE_OWNERS = {
    QUEUE_STANDARD: "sales_rep",
    QUEUE_PRIORITY: "senior_rep",
    QUEUE_HUMAN_REVIEW: "sales_manager",
    QUEUE_ESCALATION: "support_lead",
}

# How long a human has to pick up an escalation, by priority.
SLA_MINUTES = {
    PRIORITY_URGENT: 30,
    PRIORITY_HIGH: 4 * 60,
    PRIORITY_NORMAL: 24 * 60,
    PRIORITY_LOW: 48 * 60,
}


def sla_due_at(priority: str, now: Optional[datetime] = None) -> str:
    """When an escalation at this priority becomes overdue, as an ISO timestamp."""
    minutes = SLA_MINUTES.get(normalize_priority(priority), SLA_MINUTES[PRIORITY_NORMAL])
    start = now or datetime.now(timezone.utc)
    return (start + timedelta(minutes=minutes)).isoformat()


# Outbound drafts scoring at or above this are safe to send unreviewed.
OUTBOUND_APPROVE_THRESHOLD = 7.0
# Below this, a human looks at it rather than it being retried again.
OUTBOUND_ESCALATE_THRESHOLD = 4.0


@dataclass
class RoutingDecision:
    queue: str
    priority: str
    owner: str
    escalate: bool
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def _normalize(value, allowed: set, default: str) -> str:
    """Map free-text model output onto a known vocabulary.

    Models return 'Very Negative', 'neg', 'NEGATIVE' for the same thing, so an
    exact-match lookup would silently fall through to the default and quietly
    stop escalating anything.
    """
    if not value:
        return default
    text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if text in allowed:
        return text
    for candidate in allowed:
        if candidate in text:
            return candidate
    return default


def normalize_sentiment(value) -> str:
    return _normalize(value, _SENTIMENTS, SENTIMENT_NEUTRAL)


def normalize_intent(value) -> str:
    return _normalize(value, _INTENTS, INTENT_OTHER)


def normalize_priority(value) -> str:
    return _normalize(value, set(_PRIORITY_ORDER), PRIORITY_NORMAL)


def escalate_priority(priority: str) -> str:
    """Bump one level, saturating at urgent."""
    idx = _PRIORITY_ORDER.index(normalize_priority(priority))
    return _PRIORITY_ORDER[min(idx + 1, len(_PRIORITY_ORDER) - 1)]


def _owner_for(queue: str) -> str:
    return _QUEUE_OWNERS.get(queue, "sales_rep")


def _coerce_score(value, default: float = 0.0) -> float:
    """ICP fit arrives as '8 - strong fit' as often as it does as 8."""
    if isinstance(value, (int, float)):
        return float(value)
    if not value:
        return default
    digits = ""
    for char in str(value):
        if char.isdigit() or (char == "." and digits):
            digits += char
        elif digits:
            break
    try:
        return float(digits) if digits else default
    except ValueError:
        return default


def route_outbound(
    evaluation: dict,
    marketing_data: Optional[dict] = None,
    attempts_exhausted: bool = False,
) -> RoutingDecision:
    """Decide what happens to a generated outreach draft.

    A draft is only auto-approved when the evaluator scored it well. Anything
    weaker goes to a human rather than being sent, because a bad cold email is
    more expensive than a delayed one.
    """
    marketing_data = marketing_data or {}
    overall = _coerce_score(evaluation.get("overall"), default=0.0)
    verdict = str(evaluation.get("verdict", "")).strip().lower()
    icp_fit = _coerce_score(marketing_data.get("icp_fit"), default=0.0)

    if verdict == "escalate" or overall < OUTBOUND_ESCALATE_THRESHOLD:
        return RoutingDecision(
            queue=QUEUE_HUMAN_REVIEW,
            priority=PRIORITY_HIGH,
            owner=_owner_for(QUEUE_HUMAN_REVIEW),
            escalate=True,
            reason=f"draft quality {overall:.1f} below escalation threshold "
            f"{OUTBOUND_ESCALATE_THRESHOLD}",
        )

    if overall < OUTBOUND_APPROVE_THRESHOLD:
        # Revision budget is spent, but the draft is not bad enough to escalate.
        if attempts_exhausted:
            return RoutingDecision(
                queue=QUEUE_HUMAN_REVIEW,
                priority=PRIORITY_NORMAL,
                owner=_owner_for(QUEUE_HUMAN_REVIEW),
                escalate=True,
                reason=f"draft quality {overall:.1f} still below approve threshold "
                f"{OUTBOUND_APPROVE_THRESHOLD} after revision budget spent",
            )
        return RoutingDecision(
            queue=QUEUE_HUMAN_REVIEW,
            priority=PRIORITY_NORMAL,
            owner=_owner_for(QUEUE_HUMAN_REVIEW),
            escalate=True,
            reason=f"draft quality {overall:.1f} below approve threshold",
        )

    if icp_fit >= 8:
        return RoutingDecision(
            queue=QUEUE_PRIORITY,
            priority=PRIORITY_HIGH,
            owner=_owner_for(QUEUE_PRIORITY),
            escalate=False,
            reason=f"approved draft ({overall:.1f}) on a strong-fit lead (ICP {icp_fit:.0f})",
        )

    return RoutingDecision(
        queue=QUEUE_STANDARD,
        priority=PRIORITY_NORMAL,
        owner=_owner_for(QUEUE_STANDARD),
        escalate=False,
        reason=f"approved draft ({overall:.1f})",
    )


def route_inbound(triage: dict) -> RoutingDecision:
    """Decide who owns an inbound customer message.

    Intent outranks tone: a politely worded cancellation is still a churn risk,
    and a frustrated question is still just a question.
    """
    sentiment = normalize_sentiment(triage.get("sentiment"))
    intent = normalize_intent(triage.get("intent"))
    urgency = normalize_priority(triage.get("urgency"))

    if intent in _ALWAYS_ESCALATE_INTENTS:
        return RoutingDecision(
            queue=QUEUE_ESCALATION,
            priority=PRIORITY_URGENT,
            owner=_owner_for(QUEUE_ESCALATION),
            escalate=True,
            reason=f"intent '{intent}' always requires a human owner",
        )

    if sentiment == SENTIMENT_ANGRY:
        return RoutingDecision(
            queue=QUEUE_ESCALATION,
            priority=PRIORITY_URGENT,
            owner=_owner_for(QUEUE_ESCALATION),
            escalate=True,
            reason="angry sentiment requires immediate human handling",
        )

    if sentiment == SENTIMENT_NEGATIVE or intent == INTENT_COMPLAINT:
        return RoutingDecision(
            queue=QUEUE_PRIORITY,
            priority=escalate_priority(urgency),
            owner=_owner_for(QUEUE_PRIORITY),
            escalate=False,
            reason=f"negative signal (sentiment={sentiment}, intent={intent})",
        )

    if urgency == PRIORITY_URGENT:
        return RoutingDecision(
            queue=QUEUE_PRIORITY,
            priority=PRIORITY_URGENT,
            owner=_owner_for(QUEUE_PRIORITY),
            escalate=False,
            reason="message flagged urgent",
        )

    return RoutingDecision(
        queue=QUEUE_STANDARD,
        priority=urgency,
        owner=_owner_for(QUEUE_STANDARD),
        escalate=False,
        reason=f"routine message (sentiment={sentiment}, intent={intent})",
    )


def sendable(routing: dict) -> bool:
    """Whether a consumer may send this without a human first.

    The single field an integration should branch on. Absent routing means the
    item was never reviewed, which is not the same as approved.
    """
    if not routing:
        return False
    return not routing.get("escalate", True)


def front_matter(run_id: str, direction: str, routing: dict, quality_score=None) -> str:
    """Machine-readable header for a generated document.

    The prose banner tells a human; this tells a pipeline. Without it, honouring
    the review gate means parsing English out of a Markdown body, which no
    integration will do reliably.
    """
    routing = routing or {}
    ok = sendable(routing)
    lines = [
        "---",
        f"run_id: {run_id}",
        f"direction: {direction}",
        f"sendable: {'true' if ok else 'false'}",
        f"escalate: {'true' if routing.get('escalate') else 'false'}",
        f"queue: {routing.get('queue', 'unassigned')}",
        f"priority: {routing.get('priority', 'normal')}",
        f"owner: {routing.get('owner', 'unassigned')}",
    ]
    if quality_score is not None:
        lines.append(f"quality_score: {quality_score}")
    lines.append(f"generated_by: coa-agent")
    lines.append("---")
    return "\n".join(lines)
