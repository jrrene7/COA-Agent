import json
import logging
import re
from typing import List

from dotenv import load_dotenv

from lib.llm import LLM, LLMError
from lib.memory import ShortTermMemory
from lib.messages import SystemMessage, UserMessage, ToolMessage, get_tool_calls
from lib.security import UNTRUSTED_DATA_NOTICE, sanitize_text, wrap_untrusted
from lib.tooling import Tool
from lib.validation import validate_schema
from tools.web_tools import web_search, scrape_website
from agents.state import OutreachState

load_dotenv("config.env")

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a B2B Research Specialist.
Your job is to gather detailed intelligence about a company that is a potential sales lead.

Use web_search and scrape_website to collect:
- What the company does (product/service, industry, size)
- Recent news, funding, or announcements
- Key decision-makers (CEO, CTO, VP Sales, etc.)
- Visible technology stack or integrations
- Any pain points or challenges mentioned publicly

After gathering data, respond with a JSON object with these exact keys:
  profile, news, people, tech_stack, pain_points, sources

Do not guess — only include information found via tools.

""" + UNTRUSTED_DATA_NOTICE

TOOLS: List[Tool] = [web_search, scrape_website]

_MAX_TOOL_ITERATIONS = 10

_RESEARCH_SCHEMA = {
    "profile": (str, ""),
    "news": (list, []),
    "people": (list, []),
    "tech_stack": (list, []),
    "pain_points": (list, []),
    "sources": (list, []),
}


class ResearchAgentError(Exception):
    pass


class ResearchAgent:
    """Researches a lead company and returns a structured intelligence dict."""

    def __init__(self, model: str = "gpt-4o-mini", temperature: float = 0.2):
        self.llm = LLM(model=model, temperature=temperature, tools=TOOLS)

    def run(self, lead: dict) -> dict:
        """Run the research pipeline for one lead.

        Args:
            lead (dict): {"company": str, "url": str}

        Returns:
            dict: structured research data
        """
        company = sanitize_text(lead.get("company", "")).strip()
        url = sanitize_text(lead.get("url", "")).strip()

        if not company:
            raise ResearchAgentError("Lead must include a 'company' name.")

        memory = ShortTermMemory()
        memory.add(SystemMessage(content=SYSTEM_PROMPT))
        memory.add(
            UserMessage(
                content=(
                    f"Research this lead:\n"
                    f"  Company: {wrap_untrusted('lead.company', company)}\n"
                    f"  Website: {wrap_untrusted('lead.url', url)}\n\n"
                    f"Start by scraping the website, then run web searches."
                )
            )
        )

        try:
            ai_msg = self.llm.invoke(memory.get_all())
        except LLMError as exc:
            raise ResearchAgentError(f"LLM call failed: {exc}") from exc

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
                    logger.warning("ResearchAgent: unknown tool call '%s'", fn_name)
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
                raise ResearchAgentError(f"LLM call failed: {exc}") from exc

            memory.add(ai_msg)
            tool_calls = get_tool_calls(ai_msg)

        try:
            raw = ai_msg.content or "{}"
            json_match = re.search(r"\{[\s\S]*\}", raw)
            data = json.loads(json_match.group()) if json_match else {}
        except (json.JSONDecodeError, AttributeError):
            logger.warning("ResearchAgent: could not parse JSON from LLM response.")
            data = {"profile": ai_msg.content, "sources": []}

        data = validate_schema(data, _RESEARCH_SCHEMA, ResearchAgentError, "ResearchAgent")
        data["_company"] = company
        data["_url"] = url
        return data
