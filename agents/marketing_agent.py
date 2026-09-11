import json
import logging
import re
from typing import List

from dotenv import load_dotenv

from lib.llm import LLM, LLMError
from lib.memory import ShortTermMemory
from lib.messages import SystemMessage, UserMessage, ToolMessage, get_tool_calls
from lib.security import UNTRUSTED_DATA_NOTICE, wrap_untrusted
from lib.tooling import Tool
from lib.validation import validate_schema
from tools.web_tools import web_search
from agents.state import OutreachState

load_dotenv("config.env")

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a B2B Marketing Specialist with expertise in SaaS go-to-market.
You receive a company intelligence report and produce a marketing strategy for outreach.

Your output must be a JSON object with these exact keys:
  icp_fit     — score 1-10 + one sentence explaining fit
  value_props — list of 3 specific value proposition angles for this lead
  tone        — recommended communication tone (e.g. "consultative", "direct", "technical")
  hook        — one compelling opening line that references something specific about the company
  competitors — list of 2-3 competitors they likely evaluate (use web_search if unsure)
  positioning — how to position against those competitors in one sentence

Base every insight on the research data provided. Be specific — generic angles are not useful.

""" + UNTRUSTED_DATA_NOTICE

TOOLS: List[Tool] = [web_search]

_MAX_TOOL_ITERATIONS = 5

_MARKETING_SCHEMA = {
    "icp_fit": ((str, int, float), "N/A"),
    "value_props": (list, []),
    "tone": (str, "N/A"),
    "hook": (str, ""),
    "competitors": (list, []),
    "positioning": (str, ""),
}


class MarketingAgentError(Exception):
    pass


class MarketingAgent:
    """Produces a positioning and messaging strategy from research data."""

    def __init__(self, model: str = "gpt-4o-mini", temperature: float = 0.4):
        self.llm = LLM(model=model, temperature=temperature, tools=TOOLS)

    def run(self, research_data: dict) -> dict:
        """Generate marketing strategy for a researched lead.

        Args:
            research_data (dict): output from ResearchAgent.run()

        Returns:
            dict: marketing strategy dict
        """
        if not research_data:
            raise MarketingAgentError("research_data is empty.")

        company = research_data.get("_company", "the company")

        memory = ShortTermMemory()
        memory.add(SystemMessage(content=SYSTEM_PROMPT))
        memory.add(
            UserMessage(
                content=(
                    f"Create a marketing strategy for outreach to "
                    f"{wrap_untrusted('research_data._company', str(company))}.\n\n"
                    f"Research intelligence:\n"
                    f"{wrap_untrusted('research_data', json.dumps(research_data, indent=2))}\n\n"
                    f"Use web_search if you need competitor data. "
                    f"Return only the JSON object."
                )
            )
        )

        try:
            ai_msg = self.llm.invoke(memory.get_all())
        except LLMError as exc:
            raise MarketingAgentError(f"LLM call failed: {exc}") from exc

        memory.add(ai_msg)

        iterations = 0
        tool_calls = get_tool_calls(ai_msg)
        while tool_calls and iterations < _MAX_TOOL_ITERATIONS:
            iterations += 1
            for call in tool_calls:
                fn_name = call.name
                fn_args = call.arguments
                matched = next((t for t in TOOLS if t.name == fn_name), None)
                if matched:
                    try:
                        result = matched(**fn_args)
                    except Exception as exc:
                        result = {"error": str(exc)}
                    content = wrap_untrusted(f"tool:{fn_name}", json.dumps(result))
                    memory.add(
                        ToolMessage(
                            content=content,
                            tool_call_id=call.id,
                            name=fn_name,
                        )
                    )
                else:
                    logger.warning("MarketingAgent: unknown tool call '%s'", fn_name)
                    memory.add(
                        ToolMessage(
                            content=json.dumps({"error": f"unknown tool: {fn_name}"}),
                            tool_call_id=call.id,
                            name=fn_name,
                        )
                    )

            try:
                ai_msg = self.llm.invoke(memory.get_all())
            except LLMError as exc:
                raise MarketingAgentError(f"LLM call failed: {exc}") from exc

            memory.add(ai_msg)
            tool_calls = get_tool_calls(ai_msg)

        try:
            raw = ai_msg.content or "{}"
            match = re.search(r"\{[\s\S]*\}", raw)
            data = json.loads(match.group()) if match else {}
        except (json.JSONDecodeError, AttributeError):
            logger.warning("MarketingAgent: could not parse JSON from LLM response.")
            data = {"hook": ai_msg.content}

        data = validate_schema(data, _MARKETING_SCHEMA, MarketingAgentError, "MarketingAgent")
        data["_company"] = company
        return data
