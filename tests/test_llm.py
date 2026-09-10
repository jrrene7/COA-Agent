import types
import unittest
from unittest.mock import patch

from tests.external_stubs import install

install()

from lib.llm import LLM
from lib.messages import (
    AIMessage,
    SystemMessage,
    ToolMessage,
    UserMessage,
    get_tool_calls,
    to_langchain_ai_message,
)
from lib.tooling import tool


class LLMMessageTests(unittest.TestCase):
    def test_serialize_langchain_messages(self):
        ai_message = AIMessage(
            content="",
            additional_kwargs={
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "web_search",
                            "arguments": '{"query": "Acme"}',
                        },
                    }
                ]
            },
        )

        serialized = LLM._serialize(
            [
                SystemMessage(content="system"),
                UserMessage(content="human"),
                ai_message,
                ToolMessage(content="{}", tool_call_id="call_1", name="web_search"),
            ]
        )

        self.assertEqual(
            [message["role"] for message in serialized],
            ["system", "user", "assistant", "tool"],
        )
        self.assertEqual(serialized[2]["tool_calls"][0]["function"]["name"], "web_search")
        self.assertEqual(serialized[3]["tool_call_id"], "call_1")

    def test_normalizes_openai_tool_calls_into_langchain_ai_message(self):
        raw_tool_call = types.SimpleNamespace(
            id="call_1",
            function=types.SimpleNamespace(
                name="web_search",
                arguments='{"query": "Acme", "max_results": 3}',
            ),
        )

        ai_message = to_langchain_ai_message("", [raw_tool_call])
        calls = get_tool_calls(ai_message)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "web_search")
        self.assertEqual(calls[0].arguments["query"], "Acme")
        self.assertEqual(calls[0].arguments["max_results"], 3)

    def test_prefers_normalized_langchain_tool_calls_to_avoid_duplicates(self):
        ai_message = AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "call_1",
                    "name": "web_search",
                    "args": {"query": "Acme"},
                }
            ],
            additional_kwargs={
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "web_search",
                            "arguments": '{"query": "Acme"}',
                        },
                    }
                ]
            },
        )

        calls = get_tool_calls(ai_message)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "web_search")

    def test_invoke_returns_langchain_ai_message(self):
        @tool
        def fake_tool(query: str) -> dict:
            """Fake tool."""
            return {"query": query}

        class FakeCompletions:
            def __init__(self):
                self.kwargs = None

            def create(self, **kwargs):
                self.kwargs = kwargs
                message = types.SimpleNamespace(content="done", tool_calls=[])
                choice = types.SimpleNamespace(message=message)
                return types.SimpleNamespace(choices=[choice])

        completions = FakeCompletions()
        fake_client = types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=completions)
        )

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=True):
            llm = LLM(tools=[fake_tool])
        llm.client = fake_client

        result = llm.invoke([UserMessage(content="hello")])

        self.assertIsInstance(result, AIMessage)
        self.assertEqual(result.content, "done")
        self.assertEqual(completions.kwargs["messages"][0]["role"], "user")
        self.assertEqual(completions.kwargs["tools"][0]["function"]["name"], "fake_tool")


if __name__ == "__main__":
    unittest.main()
