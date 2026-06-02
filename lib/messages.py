from dataclasses import dataclass, field
from typing import List, Any, Optional


@dataclass
class SystemMessage:
    content: str
    role: str = "system"


@dataclass
class UserMessage:
    content: str
    role: str = "user"


@dataclass
class AIMessage:
    content: Optional[str]
    role: str = "assistant"
    tool_calls: List[Any] = field(default_factory=list)


@dataclass
class ToolMessage:
    content: str
    tool_call_id: str
    name: str
    role: str = "tool"
