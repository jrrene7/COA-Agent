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

SYSTEM_PROMPT = """You are a senior B2B Sales Specialist who writes high-converting outreach.
You receive research and marketing intelligence and produce personalised sales content.

Return a JSON object with these exact keys:
  email       — dict with keys: subject (str), body (str, 150-200 words, plain text)
  linkedin    — str: connection request note, max 300 characters
  followups   — list of 2 follow-up message snippets (one week apart)
  objections  — list of 3 dicts: {"objection": str, "response": str}
  next_action — str: recommended next step after sending

Rules:
- The email body must open with the hook from marketing data
- Reference at least one specific fact from the research (news, product, person)
- Never use generic openers like "I hope this finds you well"
- Keep the call to action to one clear ask (meeting, demo, or reply)
- Match the tone recommended by the marketing specialist

""" + UNTRUSTED_DATA_NOTICE


class SalesAgentError(Exception):
    pass


_SALES_SCHEMA = {
    "email": (dict, {}),
    "linkedin": (str, ""),
    "followups": (list, []),
    "objections": (list, []),
    "next_action": (str, ""),
}


class SalesAgent:
    """Produces personalised outreach content from research + marketing data."""

    def __init__(self, model: str = "gpt-4o-mini", temperature: float = 0.6):
        self.llm = LLM(model=model, temperature=temperature)

    def run(self, research_data: dict, marketing_data: dict) -> dict:
        """Generate personalised sales outreach content.

        Args:
            research_data (dict): output from ResearchAgent.run()
            marketing_data (dict): output from MarketingAgent.run()

        Returns:
            dict: sales content dict
        """
        if not research_data:
            raise SalesAgentError("research_data is empty.")
        if not marketing_data:
            raise SalesAgentError("marketing_data is empty.")

        company = research_data.get("_company", "the company")

        memory = ShortTermMemory()
        memory.add(SystemMessage(content=SYSTEM_PROMPT))
        memory.add(
            UserMessage(
                content=(
                    f"Write personalised outreach for "
                    f"{wrap_untrusted('research_data._company', str(company))}.\n\n"
                    f"--- Research Data ---\n"
                    f"{wrap_untrusted('research_data', json.dumps(research_data, indent=2))}\n\n"
                    f"--- Marketing Strategy ---\n"
                    f"{wrap_untrusted('marketing_data', json.dumps(marketing_data, indent=2))}\n\n"
                    f"Return only the JSON object."
                )
            )
        )

        try:
            ai_msg = self.llm.invoke(memory.get_all())
        except LLMError as exc:
            raise SalesAgentError(f"LLM call failed: {exc}") from exc

        try:
            raw = ai_msg.content or "{}"
            match = re.search(r"\{[\s\S]*\}", raw)
            data = json.loads(match.group()) if match else {}
        except (json.JSONDecodeError, AttributeError):
            logger.warning("SalesAgent: could not parse JSON from LLM response.")
            data = {"email": {"subject": "Follow-up", "body": ai_msg.content}}

        data = validate_schema(data, _SALES_SCHEMA, SalesAgentError, "SalesAgent")
        data["_company"] = company
        return data
