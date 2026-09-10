import json
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

UserMessage = HumanMessage


@dataclass(frozen=True)
class ToolCallRequest:
    id: str
    name: str
    arguments: dict


def get_tool_calls(message: AIMessage) -> list[ToolCallRequest]:
    """Return LangChain or OpenAI tool calls in one project-friendly shape."""
    raw_calls = list(getattr(message, "tool_calls", []) or [])
    if not raw_calls:
        additional = getattr(message, "additional_kwargs", {}) or {}
        raw_calls.extend(additional.get("tool_calls") or [])
    return [_normalize_tool_call(call) for call in raw_calls]


def to_langchain_ai_message(
    content: Optional[str],
    raw_tool_calls: Iterable[Any],
) -> AIMessage:
    """Build an AIMessage while preserving provider tool-call metadata."""
    return AIMessage(
        content=content or "",
        additional_kwargs={
            "tool_calls": [
                _to_openai_tool_call_dict(call) for call in raw_tool_calls
            ]
        },
    )


def to_openai_tool_call(call: ToolCallRequest) -> dict:
    return {
        "id": call.id,
        "type": "function",
        "function": {
            "name": call.name,
            "arguments": json.dumps(call.arguments),
        },
    }


def _normalize_tool_call(call: Any) -> ToolCallRequest:
    if isinstance(call, ToolCallRequest):
        return call

    if isinstance(call, dict):
        if "function" in call:
            function = call.get("function") or {}
            return ToolCallRequest(
                id=call.get("id", ""),
                name=function.get("name", ""),
                arguments=_parse_arguments(function.get("arguments", {})),
            )
        return ToolCallRequest(
            id=call.get("id", ""),
            name=call.get("name", ""),
            arguments=_parse_arguments(call.get("args", {})),
        )

    function = getattr(call, "function", None)
    if function is not None:
        return ToolCallRequest(
            id=getattr(call, "id", ""),
            name=getattr(function, "name", ""),
            arguments=_parse_arguments(getattr(function, "arguments", {})),
        )

    return ToolCallRequest(
        id=getattr(call, "id", ""),
        name=getattr(call, "name", ""),
        arguments=_parse_arguments(getattr(call, "args", {})),
    )


def _to_openai_tool_call_dict(call: Any) -> dict:
    if isinstance(call, dict) and "function" in call:
        return call

    normalized = _normalize_tool_call(call)
    return to_openai_tool_call(normalized)


def _parse_arguments(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
