import json
import logging
import re

from dotenv import load_dotenv

from lib.llm import LLM, LLMError
from lib.memory import ShortTermMemory
from lib.messages import SystemMessage, UserMessage
from lib.security import UNTRUSTED_DATA_NOTICE, sanitize_text, wrap_untrusted
from lib.validation import validate_schema

load_dotenv("config.env")

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a customer support triage specialist.
You read one inbound customer message and classify it so it can be routed.

Return a JSON object with these exact keys:
  sentiment   — how the customer feels: one of "positive", "neutral", "negative", "angry"
  intent      — what they want: one of "question", "interested", "complaint",
                "churn_risk", "billing", "unsubscribe", "other"
  urgency     — one of "low", "normal", "high", "urgent"
  summary     — one sentence, neutral tone, describing what they are asking for
  key_points  — list of the concrete facts or asks in the message
  entities    — dict of anything identifying mentioned: {"order_id": ..., "product": ...}

Classification guidance:
- "angry" is for hostility or threats, not mere frustration. Frustrated but civil
  is "negative".
- "churn_risk" covers any signal they are considering leaving: cancelling,
  comparing competitors, or citing repeated failures — even when worded politely.
- "unsubscribe" is a request to stop being contacted, which is distinct from
  cancelling a paid product.
- Judge urgency by business impact stated in the message, not by tone.

""" + UNTRUSTED_DATA_NOTICE

_TRIAGE_SCHEMA = {
    "sentiment": (str, "neutral"),
    "intent": (str, "other"),
    "urgency": (str, "normal"),
    "summary": (str, ""),
    "key_points": (list, []),
    "entities": (dict, {}),
}


class TriageAgentError(Exception):
    pass


class TriageAgent:
    """Classifies an inbound customer message: sentiment, intent, urgency."""

    def __init__(self, model: str = "gpt-4o-mini", temperature: float = 0.0):
        # Temperature 0: triage feeds routing, which should be reproducible.
        self.llm = LLM(model=model, temperature=temperature)

    def run(self, message: dict) -> dict:
        """Triage one inbound message.

        Args:
            message (dict): {"sender": str, "subject": str, "body": str, "channel": str}

        Returns:
            dict: triage signals consumed by lib.routing.route_inbound
        """
        body = sanitize_text(str(message.get("body", ""))).strip()
        if not body:
            raise TriageAgentError("Inbound message must include a body.")

        subject = sanitize_text(str(message.get("subject", ""))).strip()
        channel = sanitize_text(str(message.get("channel", "email"))).strip()

        memory = ShortTermMemory()
        memory.add(SystemMessage(content=SYSTEM_PROMPT))
        memory.add(
            UserMessage(
                content=(
                    f"Triage this inbound message.\n\n"
                    f"Channel: {wrap_untrusted('message.channel', channel)}\n"
                    f"Subject: {wrap_untrusted('message.subject', subject)}\n"
                    f"Body:\n{wrap_untrusted('message.body', body)}\n\n"
                    f"Return only the JSON object."
                )
            )
        )

        try:
            ai_msg = self.llm.invoke(memory.get_all())
        except LLMError as exc:
            raise TriageAgentError(f"LLM call failed: {exc}") from exc

        try:
            raw = ai_msg.content or "{}"
            match = re.search(r"\{[\s\S]*\}", raw)
            data = json.loads(match.group()) if match else {}
        except (json.JSONDecodeError, AttributeError):
            logger.warning("TriageAgent: could not parse JSON from LLM response.")
            data = {}

        return validate_schema(data, _TRIAGE_SCHEMA, TriageAgentError, "TriageAgent")
