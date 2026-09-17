from typing import TypedDict


class OutreachState(TypedDict):
    """Outbound pipeline: research a lead and produce reviewed outreach."""

    run_id: str            # unique id for this pipeline execution
    lead: dict             # {"company": str, "url": str}
    research_data: dict    # web intel + company profile
    marketing_data: dict   # positioning + messaging angles
    sales_data: dict       # email draft + objection tips
    evaluation: dict       # sentiment, scores, overall, issues, critique, verdict
    critique: str          # evaluator feedback carried into the marketing retry
    routing: dict          # queue, priority, owner, escalate, reason
    report_path: str       # absolute path of written report
    feedback_attempts: int # times sales -> marketing feedback loop has run


class InboundState(TypedDict):
    """Inbound pipeline: triage a customer message, route it, draft a reply."""

    run_id: str            # unique id for this pipeline execution
    message: dict          # {"sender": str, "subject": str, "body": str, "channel": str}
    triage: dict           # sentiment, intent, urgency, summary, key_points
    routing: dict          # queue, priority, owner, escalate, reason
    reply: dict            # drafted reply: subject, body, tone
    handoff: dict          # context handed to a human when escalated
    report_path: str       # absolute path of written handoff/reply note
