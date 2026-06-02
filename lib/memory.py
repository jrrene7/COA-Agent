from typing import List, Any

from lib.messages import SystemMessage


class ShortTermMemory:
    """Rolling in-memory message history that preserves the system message."""

    def __init__(self, max_messages: int = 50):
        self.max_messages = max_messages
        self.messages: List[Any] = []

    def add(self, message: Any) -> None:
        self.messages.append(message)
        if len(self.messages) > self.max_messages:
            system = [m for m in self.messages if isinstance(m, SystemMessage)]
            rest = [m for m in self.messages if not isinstance(m, SystemMessage)]
            keep = self.max_messages - len(system)
            self.messages = system + rest[-keep:]

    def get_all(self) -> List[Any]:
        return list(self.messages)

    def clear(self) -> None:
        self.messages = []
