import json
import logging
import re

from dotenv import load_dotenv

from lib.llm import LLM, LLMError
from lib.memory import ShortTermMemory
from lib.messages import SystemMessage, UserMessage
from lib.security import UNTRUSTED_DATA_NOTICE, wrap_untrusted
from lib.validation import validate_schema

load_dotenv("config.env")

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a demanding B2B outreach quality reviewer.
You receive a generated cold email plus the research and marketing brief it was
written from, and you judge whether it is fit to send.

Return a JSON object with these exact keys:
  sentiment       — how the email reads to its recipient: one of
                    "positive", "neutral", "negative", "angry"
  scores          — dict with integer 1-10 values for these exact keys:
                      personalisation — does it reference specific, verifiable facts?
                      tone_fit        — does it match the tone the brief asked for?
                      clarity         — is the ask unambiguous?
                      cta_strength    — is there exactly one clear call to action?
  overall         — integer 1-10, your holistic judgement of whether to send
  issues          — list of specific, actionable problems (empty if none)
  critique        — one paragraph telling the marketing specialist what to change.
                    Write it as instructions, not as praise. Empty string if approving.
  verdict         — one of "approve", "revise", "escalate"

Scoring guidance:
- Generic flattery, invented facts, or claims not supported by the research are
  serious defects. Score personalisation low and say so.
- More than one call to action, or none, caps cta_strength at 4.
- Use "escalate" when the draft is unsalvageable or the underlying research is
  too thin to write from — not merely because the draft is mediocre.
- Be strict. A 7+ overall means you would send this to a real prospect yourself.

""" + UNTRUSTED_DATA_NOTICE

_EVALUATION_SCHEMA = {
    "sentiment": (str, "neutral"),
    "scores": (dict, {}),
    "overall": ((int, float), 0),
    "issues": (list, []),
    "critique": (str, ""),
    "verdict": (str, "revise"),
}


class EvaluatorAgentError(Exception):
    pass


class EvaluatorAgent:
    """Scores a generated outreach draft and says whether it should be sent."""

    def __init__(self, model: str = "gpt-4o-mini", temperature: float = 0.0):
        # Temperature 0: the gate should be as reproducible as an LLM gate can be.
        self.llm = LLM(model=model, temperature=temperature)

    def run(self, research_data: dict, marketing_data: dict, sales_data: dict) -> dict:
        """Evaluate a sales draft.

        Args:
            research_data (dict): output from ResearchAgent.run()
            marketing_data (dict): output from MarketingAgent.run()
            sales_data (dict): output from SalesAgent.run()

        Returns:
            dict: evaluation with sentiment, scores, overall, issues, critique, verdict
        """
        if not sales_data:
            raise EvaluatorAgentError("sales_data is empty.")

        email = sales_data.get("email") or {}
        subject = str(email.get("subject", "")).strip()
        body = str(email.get("body", "")).strip()

        # A structurally empty draft needs no model call to judge.
        if not subject or not body:
            return {
                "sentiment": "neutral",
                "scores": {},
                "overall": 0,
                "issues": ["Email is missing a subject or body."],
                "critique": (
                    "The sales draft came back without a usable subject or body. "
                    "Produce a sharper, more concrete brief — a specific hook tied "
                    "to a verifiable fact, and an explicit tone — so the draft has "
                    "something to build on."
                ),
                "verdict": "revise",
            }

        memory = ShortTermMemory()
        memory.add(SystemMessage(content=SYSTEM_PROMPT))
        memory.add(
            UserMessage(
                content=(
                    f"--- Research brief ---\n"
                    f"{wrap_untrusted('research_data', json.dumps(research_data, indent=2))}\n\n"
                    f"--- Marketing brief ---\n"
                    f"{wrap_untrusted('marketing_data', json.dumps(marketing_data, indent=2))}\n\n"
                    f"--- Draft to review ---\n"
                    f"{wrap_untrusted('sales_data', json.dumps(sales_data, indent=2))}\n\n"
                    f"Return only the JSON object."
                )
            )
        )

        try:
            ai_msg = self.llm.invoke(memory.get_all())
        except LLMError as exc:
            raise EvaluatorAgentError(f"LLM call failed: {exc}") from exc

        try:
            raw = ai_msg.content or "{}"
            match = re.search(r"\{[\s\S]*\}", raw)
            data = json.loads(match.group()) if match else {}
        except (json.JSONDecodeError, AttributeError):
            logger.warning("EvaluatorAgent: could not parse JSON from LLM response.")
            data = {}

        data = validate_schema(
            data, _EVALUATION_SCHEMA, EvaluatorAgentError, "EvaluatorAgent"
        )

        verdict = str(data.get("verdict", "")).strip().lower()
        if verdict not in ("approve", "revise", "escalate"):
            # An unparseable verdict must not read as approval.
            logger.warning("EvaluatorAgent: unknown verdict %r, treating as revise.", verdict)
            verdict = "revise"
        data["verdict"] = verdict

        return data
