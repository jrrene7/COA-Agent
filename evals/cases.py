"""Labelled outreach drafts for evaluating the EvaluatorAgent.

Cases are deliberately unambiguous: a competent human reviewer should agree with
every label without hesitation. That is the point — when the evaluator disagrees
with one of these, the evaluator is wrong, not the label. Borderline drafts are
where reasonable reviewers differ, so they belong in threshold tuning rather
than in a pass/fail corpus.

Each case pairs a draft with the research and marketing brief it was supposedly
written from, because "is this personalised?" is only answerable against the
brief.
"""

from dataclasses import dataclass, field

LABEL_SEND = "send"
LABEL_REVIEW = "review"


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    label: str           # LABEL_SEND or LABEL_REVIEW
    rationale: str       # why a human would label it this way
    research: dict
    marketing: dict
    sales: dict
    failure_mode: str = ""   # for review cases: what specifically is wrong
    tags: tuple = field(default_factory=tuple)


_RESEARCH_NORTHWIND = {
    "_company": "Northwind Logistics",
    "profile": "Northwind Logistics is a mid-market freight brokerage with about "
               "400 employees, coordinating truckload shipping across the US Midwest.",
    "news": ["Raised a $40M Series C in March to expand its carrier network",
             "Opened a second operations hub in Columbus"],
    "people": ["Dana Whitfield, VP Operations", "Marcus Lee, CTO"],
    "tech_stack": ["Salesforce", "a homegrown TMS", "Snowflake"],
    "pain_points": ["Manual carrier matching", "Dispatchers reconcile loads by hand"],
    "sources": ["https://northwind.example/news"],
}

_MARKETING_NORTHWIND = {
    "_company": "Northwind Logistics",
    "icp_fit": "9 - strong fit; mid-market brokerage with manual ops and fresh funding",
    "value_props": ["Cut dispatcher reconciliation time",
                    "Automate carrier matching against historical lane performance",
                    "Feed Snowflake without custom ETL"],
    "tone": "consultative",
    "hook": "The Series C and the Columbus hub both point at the same bottleneck: "
            "manual carrier matching.",
    "competitors": ["Convoy", "project44"],
    "positioning": "Lead with depth of lane data rather than breadth of network.",
}


CASES = (
    # ---------------- Drafts a reviewer would send ----------------
    EvalCase(
        case_id="send_specific_hook",
        label=LABEL_SEND,
        rationale="Opens on a verifiable fact from research, ties it to a real "
                  "pain point, consultative tone as briefed, exactly one ask.",
        tags=("personalised", "single-cta"),
        research=_RESEARCH_NORTHWIND,
        marketing=_MARKETING_NORTHWIND,
        sales={
            "email": {
                "subject": "Columbus hub + manual carrier matching",
                "body": (
                    "Dana — opening a second ops hub in Columbus right after the "
                    "Series C suggests you're scaling volume faster than the "
                    "dispatch process was built for.\n\n"
                    "Most brokerages we work with hit the same wall: carrier "
                    "matching stays manual, so every new lane adds reconciliation "
                    "work rather than amortising it. Teams running a homegrown TMS "
                    "alongside Snowflake usually have the lane history to automate "
                    "the match — it just isn't reachable from the dispatch screen.\n\n"
                    "Worth a 20-minute call to see whether your lane data is in "
                    "good enough shape for that? Happy to look at a sample first."
                ),
            },
            "linkedin": "Saw the Columbus expansion — curious how dispatch is scaling with it.",
            "followups": ["Following up on the dispatch question.", "Closing the loop."],
            "objections": [{"objection": "We just raised, not buying",
                            "response": "Fair — worth a look before headcount planning."}],
            "next_action": "Send a 20-minute call invite.",
        },
    ),
    EvalCase(
        case_id="send_technical_concise",
        label=LABEL_SEND,
        rationale="Short, technically specific, references the actual stack, one "
                  "clear ask. Brevity is not a defect.",
        tags=("personalised", "single-cta", "short"),
        research=_RESEARCH_NORTHWIND,
        marketing={**_MARKETING_NORTHWIND, "tone": "direct, technical"},
        sales={
            "email": {
                "subject": "Snowflake → dispatch, without the ETL",
                "body": (
                    "Marcus — you're running a homegrown TMS with Snowflake behind "
                    "it. The usual blocker for automated carrier matching isn't the "
                    "model, it's getting lane history back out of the warehouse and "
                    "into dispatch without building a pipeline nobody owns.\n\n"
                    "We read from Snowflake directly and write matches back to the "
                    "TMS over its API. No ETL to maintain.\n\n"
                    "Can I send over the integration spec?"
                ),
            },
            "linkedin": "Question about your TMS/Snowflake setup.",
            "followups": ["Bumping this.", "Last note from me."],
            "objections": [{"objection": "Built in-house", "response": "Most have; the gap is usually refresh latency."}],
            "next_action": "Send integration spec.",
        },
    ),

    # ---------------- Drafts a reviewer would stop ----------------
    EvalCase(
        case_id="review_generic",
        label=LABEL_REVIEW,
        failure_mode="Could be sent to any company; no fact from research appears.",
        rationale="Zero personalisation. Nothing here is specific to Northwind.",
        tags=("generic",),
        research=_RESEARCH_NORTHWIND,
        marketing=_MARKETING_NORTHWIND,
        sales={
            "email": {
                "subject": "Quick question",
                "body": (
                    "Hi there,\n\nI hope this email finds you well. I'm reaching out "
                    "because we help companies like yours improve efficiency and "
                    "reduce costs with our industry-leading platform.\n\n"
                    "Many businesses in your space struggle with operational "
                    "challenges, and we've helped organisations achieve significant "
                    "ROI.\n\nWould you be open to a quick chat?"
                ),
            },
            "linkedin": "Would love to connect!",
            "followups": ["Just bumping this up.", "Circling back."],
            "objections": [],
            "next_action": "Follow up.",
        },
    ),
    EvalCase(
        case_id="review_hallucinated_fact",
        label=LABEL_REVIEW,
        failure_mode="Invents a Series D, a named customer, and a 40% figure that "
                     "appear nowhere in the research.",
        rationale="Fabricated specifics are worse than generic ones — they are "
                  "confidently wrong and the prospect will know it.",
        tags=("hallucination",),
        research=_RESEARCH_NORTHWIND,
        marketing=_MARKETING_NORTHWIND,
        sales={
            "email": {
                "subject": "Congrats on the Series D",
                "body": (
                    "Dana — huge congratulations on the $120M Series D last month, "
                    "and on landing Walmart as an anchor shipper. That kind of volume "
                    "step-change is exactly when manual dispatch breaks.\n\n"
                    "When we worked with your competitor Ryder, we cut their "
                    "reconciliation time by 40% in six weeks, and I'd expect similar "
                    "at your scale given the 900 dispatchers you're now running.\n\n"
                    "Free Thursday?"
                ),
            },
            "linkedin": "Congrats on the Series D!",
            "followups": ["Following up."],
            "objections": [],
            "next_action": "Book Thursday.",
        },
    ),
    EvalCase(
        case_id="review_multiple_ctas",
        label=LABEL_REVIEW,
        failure_mode="Four competing asks; the recipient cannot tell what to do.",
        rationale="Personalisation is fine but the close is incoherent.",
        tags=("multi-cta",),
        research=_RESEARCH_NORTHWIND,
        marketing=_MARKETING_NORTHWIND,
        sales={
            "email": {
                "subject": "Carrier matching at Northwind",
                "body": (
                    "Dana — the Columbus hub opening suggests dispatch volume is "
                    "climbing faster than the manual matching process can absorb.\n\n"
                    "Could you reply with a good time this week? Or book directly on "
                    "my calendar — link below. Alternatively, download our logistics "
                    "benchmark report, or forward this to whoever owns dispatch "
                    "tooling. If it's easier, just reply 'send info' and I'll mail "
                    "the deck. Also happy to do a live demo Thursday or Friday."
                ),
            },
            "linkedin": "Connect?",
            "followups": ["Bump."],
            "objections": [],
            "next_action": "Whatever they pick.",
        },
    ),
    EvalCase(
        case_id="review_no_cta",
        label=LABEL_REVIEW,
        failure_mode="Ends with no ask at all; nothing for the prospect to act on.",
        rationale="Well-written and personalised but does not advance anything.",
        tags=("no-cta",),
        research=_RESEARCH_NORTHWIND,
        marketing=_MARKETING_NORTHWIND,
        sales={
            "email": {
                "subject": "Thoughts on dispatch automation",
                "body": (
                    "Dana — the Series C and the Columbus hub both point at rising "
                    "load volume, and manual carrier matching tends to be the first "
                    "thing that buckles.\n\n"
                    "We've spent a lot of time on lane-history matching for "
                    "brokerages running homegrown TMS platforms, and the pattern is "
                    "fairly consistent: the data is already there, it just isn't "
                    "reachable at dispatch time.\n\n"
                    "Anyway, thought it might be of interest. Have a great week."
                ),
            },
            "linkedin": "Connecting.",
            "followups": [],
            "objections": [],
            "next_action": "None.",
        },
    ),
    EvalCase(
        case_id="review_wrong_tone",
        label=LABEL_REVIEW,
        failure_mode="Aggressive and presumptuous where the brief asked for "
                     "consultative; insults the prospect's current process.",
        rationale="Tone mismatch severe enough to damage the relationship.",
        tags=("tone",),
        research=_RESEARCH_NORTHWIND,
        marketing=_MARKETING_NORTHWIND,
        sales={
            "email": {
                "subject": "You're losing money every single day",
                "body": (
                    "Dana — let's be blunt. Your dispatchers are reconciling loads by "
                    "hand in 2026. That's indefensible at 400 people, and every day "
                    "you delay is money burned.\n\n"
                    "Your competitors already automated this. You raised $40M and "
                    "you're still running spreadsheets — investors will ask.\n\n"
                    "I'm going to assume you want to fix this. Reply with a time or "
                    "I'll assume you're not serious about scaling."
                ),
            },
            "linkedin": "You need to see this.",
            "followups": ["Still waiting."],
            "objections": [],
            "next_action": "Push harder.",
        },
    ),
    EvalCase(
        case_id="review_too_thin",
        label=LABEL_REVIEW,
        failure_mode="Two sentences with no substance; not a usable outreach email.",
        rationale="Structurally present but functionally empty.",
        tags=("thin",),
        research=_RESEARCH_NORTHWIND,
        marketing=_MARKETING_NORTHWIND,
        sales={
            "email": {
                "subject": "Hi",
                "body": "Hi Dana, I'd love to talk about logistics software. Let me know!",
            },
            "linkedin": "Hi.",
            "followups": [],
            "objections": [],
            "next_action": "Wait.",
        },
    ),
)


def cases_by_label(label: str) -> tuple:
    return tuple(case for case in CASES if case.label == label)
