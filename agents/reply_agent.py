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

SYSTEM_PROMPT = """You are a customer support specialist drafting a reply.
You receive an inbound customer message and its triage classification, and you
write a reply for a human to review and send.

Return a JSON object with these exact keys:
  subject       — reply subject line
  body          — the reply, 80-150 words, plain text
  tone          — the tone you chose, and why, in one short phrase
  confidence    — integer 1-10: how confident you are this fully answers them
  open_questions— list of things you could not answer from the information given

Rules:
- Acknowledge the specific issue they raised. Never open with filler like
  "I hope this finds you well".
- If you do not have the information to answer, say so plainly in the body and
  list it in open_questions. Never invent order numbers, dates, refund amounts,
  policies, or account details.
- Match the customer's register: brief for brief, thorough for detailed.
- Do not promise anything you were not told is true. No compensation offers.
- One clear next step.

""" + UNTRUSTED_DATA_NOTICE

_REPLY_SCHEMA = {
    "subject": (str, ""),
    "body": (str, ""),
    "tone": (str, ""),
    "confidence": ((int, float), 0),
    "open_questions": (list, []),
}


class ReplyAgentError(Exception):
    pass


class ReplyAgent:
    """Drafts a support reply. Output is always a draft for human review."""

    def __init__(self, model: str = "gpt-4o-mini", temperature: float = 0.4):
        self.llm = LLM(model=model, temperature=temperature)

    def run(self, message: dict, triage: dict) -> dict:
        """Draft a reply to an inbound message.

        Args:
            message (dict): the inbound message
            triage (dict): output from TriageAgent.run()

        Returns:
            dict: reply draft — never sent automatically
        """
        body = sanitize_text(str(message.get("body", ""))).strip()
        if not body:
            raise ReplyAgentError("Inbound message must include a body.")
        if not triage:
            raise ReplyAgentError("triage is empty.")

        subject = sanitize_text(str(message.get("subject", ""))).strip()

        memory = ShortTermMemory()
        memory.add(SystemMessage(content=SYSTEM_PROMPT))
        memory.add(
            UserMessage(
                content=(
                    f"--- Customer message ---\n"
                    f"Subject: {wrap_untrusted('message.subject', subject)}\n"
                    f"Body:\n{wrap_untrusted('message.body', body)}\n\n"
                    f"--- Triage ---\n"
                    f"{wrap_untrusted('triage', json.dumps(triage, indent=2))}\n\n"
                    f"Return only the JSON object."
                )
            )
        )

        try:
            ai_msg = self.llm.invoke(memory.get_all())
        except LLMError as exc:
            raise ReplyAgentError(f"LLM call failed: {exc}") from exc

        try:
            raw = ai_msg.content or "{}"
            match = re.search(r"\{[\s\S]*\}", raw)
            data = json.loads(match.group()) if match else {}
        except (json.JSONDecodeError, AttributeError):
            logger.warning("ReplyAgent: could not parse JSON from LLM response.")
            data = {}

        return validate_schema(data, _REPLY_SCHEMA, ReplyAgentError, "ReplyAgent")
