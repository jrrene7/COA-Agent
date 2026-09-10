import os
import time
import logging
from typing import List, Optional

from openai import OpenAI, RateLimitError, APIError, APITimeoutError

from lib.logging_config import redact_text
from lib.messages import (
    AIMessage,
    SystemMessage,
    ToolMessage,
    UserMessage,
    get_tool_calls,
    to_langchain_ai_message,
    to_openai_tool_call,
)
from lib.tooling import Tool

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_BACKOFF = 2.0
_DEFAULT_BASE_URL = "https://api.openai.com/v1"


class LLMError(Exception):
    pass


class LLM:
    """Thin wrapper around the OpenAI chat completions API with retry logic."""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        temperature: float = 0.3,
        tools: Optional[List[Tool]] = None,
    ):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise LLMError("OPENAI_API_KEY is not set.")

        base_url = (
            os.getenv("OPENAI_BASE_URL", _DEFAULT_BASE_URL).strip()
            or _DEFAULT_BASE_URL
        )

        self.model = model
        self.temperature = temperature
        self.tools = tools or []
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    
    # Internal helpers
    @staticmethod
    def _serialize(messages: list) -> list:
        out = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                out.append({"role": "system", "content": msg.content})

            elif isinstance(msg, UserMessage):
                out.append({"role": "user", "content": msg.content})

            elif isinstance(msg, AIMessage):
                entry: dict = {"role": "assistant", "content": msg.content}
                tool_calls = get_tool_calls(msg)
                if tool_calls:
                    entry["tool_calls"] = [
                        to_openai_tool_call(call) for call in tool_calls
                    ]
                out.append(entry)

            elif isinstance(msg, ToolMessage):
                out.append(
                    {
                        "role": "tool",
                        "content": msg.content,
                        "tool_call_id": msg.tool_call_id,
                        "name": msg.name,
                    }
                )
        return out

    
    # Public API
    def invoke(self, messages: list) -> AIMessage:
        """Send messages to the LLM and return an AIMessage.

        Retries up to _MAX_RETRIES times on transient API errors.
        """
        serialized = self._serialize(messages)
        kwargs: dict = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": serialized,
        }
        if self.tools:
            kwargs["tools"] = [t.to_openai_schema() for t in self.tools]

        last_exc: Optional[Exception] = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = self.client.chat.completions.create(**kwargs)
                msg = response.choices[0].message
                return to_langchain_ai_message(
                    content=msg.content,
                    raw_tool_calls=msg.tool_calls or [],
                )
            except RateLimitError as exc:
                wait = _RETRY_BACKOFF ** attempt
                logger.warning(
                    "Rate limit hit (attempt %d/%d). Retrying in %.1fs.",
                    attempt,
                    _MAX_RETRIES,
                    wait,
                )
                last_exc = exc
                time.sleep(wait)
            except APITimeoutError as exc:
                wait = _RETRY_BACKOFF ** attempt
                logger.warning(
                    "API timeout (attempt %d/%d). Retrying in %.1fs.",
                    attempt,
                    _MAX_RETRIES,
                    wait,
                )
                last_exc = exc
                time.sleep(wait)
            except APIError as exc:
                raise LLMError(f"OpenAI API error: {redact_text(str(exc))}") from exc

        message = redact_text(str(last_exc)) if last_exc else "unknown error"
        raise LLMError(f"LLM call failed after {_MAX_RETRIES} retries: {message}") from last_exc
