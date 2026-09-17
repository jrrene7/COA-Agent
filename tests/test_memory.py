import unittest

from tests.external_stubs import install

install()

from lib.memory import ShortTermMemory
from lib.messages import SystemMessage, UserMessage


class ShortTermMemoryTests(unittest.TestCase):
    def test_preserves_order_below_limit(self):
        memory = ShortTermMemory(max_messages=10)
        memory.add(SystemMessage(content="system"))
        memory.add(UserMessage(content="first"))
        memory.add(UserMessage(content="second"))

        self.assertEqual(
            [m.content for m in memory.get_all()], ["system", "first", "second"]
        )

    def test_evicts_oldest_non_system_messages_past_limit(self):
        memory = ShortTermMemory(max_messages=4)
        memory.add(SystemMessage(content="system"))
        for i in range(6):
            memory.add(UserMessage(content=f"msg{i}"))

        contents = [m.content for m in memory.get_all()]
        self.assertEqual(len(contents), 4)
        self.assertEqual(contents, ["system", "msg3", "msg4", "msg5"])

    def test_system_message_survives_eviction(self):
        """The system prompt carries UNTRUSTED_DATA_NOTICE — dropping it would
        silently disable the agent's prompt-injection defense mid-run."""
        memory = ShortTermMemory(max_messages=3)
        memory.add(SystemMessage(content="system"))
        for i in range(20):
            memory.add(UserMessage(content=f"msg{i}"))

        kept = memory.get_all()
        self.assertIsInstance(kept[0], SystemMessage)
        self.assertEqual(kept[0].content, "system")

    def test_get_all_returns_a_copy(self):
        memory = ShortTermMemory()
        memory.add(UserMessage(content="only"))

        memory.get_all().append(UserMessage(content="mutated"))

        self.assertEqual(len(memory.get_all()), 1)

    def test_clear_empties_history(self):
        memory = ShortTermMemory()
        memory.add(SystemMessage(content="system"))
        memory.add(UserMessage(content="first"))

        memory.clear()

        self.assertEqual(memory.get_all(), [])


if __name__ == "__main__":
    unittest.main()
