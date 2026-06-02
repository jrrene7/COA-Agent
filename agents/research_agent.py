import json
import logging
import re
from typing import List

from dotenv import load_dotenv

from lib.llm import LLM, LLMError
from lib.messages import SystemMessage, UserMessage, ToolMessage, AIMessage
from lib.tooling import Tool
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

Do not guess — only include information found via tools."""

TOOLS: List[Tool] = [web_search, scrape_website]

_MAX_TOOL_ITERATIONS = 10


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
        company = lead.get("company", "").strip()
        url = lead.get("url", "").strip()

        if not company:
            raise ResearchAgentError("Lead must include a 'company' name.")

        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            UserMessage(
                content=(
                    f"Research this lead:\n"
                    f"  Company: {company}\n"
                    f"  Website: {url}\n\n"
                    f"Start by scraping the website, then run web searches."
                )
            ),
        ]

        try:
            ai_msg = self.llm.invoke(messages)
        except LLMError as exc:
            raise ResearchAgentError(f"LLM call failed: {exc}") from exc

        messages.append(ai_msg)

        iterations = 0
        while ai_msg.tool_calls and iterations < _MAX_TOOL_ITERATIONS:
            iterations += 1
            for call in ai_msg.tool_calls:
                fn_name = call.function.name
                try:
                    fn_args = json.loads(call.function.arguments)
                except json.JSONDecodeError:
                    fn_args = {}

                matched = next((t for t in TOOLS if t.name == fn_name), None)
                if matched:
                    try:
                        result = matched(**fn_args)
                    except Exception as exc:
                        result = {"error": str(exc)}
                    messages.append(
                        ToolMessage(
                            content=json.dumps(result),
                            tool_call_id=call.id,
                            name=fn_name,
                        )
                    )
                else:
                    logger.warning("ResearchAgent: unknown tool call '%s'", fn_name)

            try:
                ai_msg = self.llm.invoke(messages)
            except LLMError as exc:
                raise ResearchAgentError(f"LLM call failed: {exc}") from exc

            messages.append(ai_msg)

        try:
            raw = ai_msg.content or "{}"
            json_match = re.search(r"\{[\s\S]*\}", raw)
            data = json.loads(json_match.group()) if json_match else {}
        except (json.JSONDecodeError, AttributeError):
            logger.warning("ResearchAgent: could not parse JSON from LLM response.")
            data = {"profile": ai_msg.content, "sources": []}

        data["_company"] = company
        data["_url"] = url
        return data
