"""Test-only stubs for optional runtime dependencies.

The unit suite exercises local business logic and should not require API
packages or credentials. These stubs are installed before importing modules
that reference LangGraph, LangChain Core, OpenAI, Tavily, requests, or
python-dotenv.
"""

from __future__ import annotations

import sys
import types


def install() -> None:
    langchain_core = types.ModuleType("langchain_core")
    langchain_messages = types.ModuleType("langchain_core.messages")

    class _BaseMessage:
        def __init__(self, content="", **kwargs):
            self.content = content
            self.additional_kwargs = kwargs.pop("additional_kwargs", {})
            for key, value in kwargs.items():
                setattr(self, key, value)

    class _HumanMessage(_BaseMessage):
        pass

    class _SystemMessage(_BaseMessage):
        pass

    class _AIMessage(_BaseMessage):
        def __init__(self, content="", tool_calls=None, **kwargs):
            super().__init__(content=content, **kwargs)
            self.tool_calls = tool_calls or []

    class _ToolMessage(_BaseMessage):
        def __init__(self, content="", tool_call_id="", name="", **kwargs):
            super().__init__(content=content, **kwargs)
            self.tool_call_id = tool_call_id
            self.name = name

    langchain_messages.AIMessage = _AIMessage
    langchain_messages.HumanMessage = _HumanMessage
    langchain_messages.SystemMessage = _SystemMessage
    langchain_messages.ToolMessage = _ToolMessage
    langchain_core.messages = langchain_messages
    sys.modules.setdefault("langchain_core", langchain_core)
    sys.modules.setdefault("langchain_core.messages", langchain_messages)

    langgraph = types.ModuleType("langgraph")
    langgraph_graph = types.ModuleType("langgraph.graph")
    langgraph_graph.START = "__start__"
    langgraph_graph.END = "__end__"

    class _CompiledGraph:
        def __init__(self, nodes, edges):
            self._nodes = nodes
            self._edges = edges

        def stream(self, initial_state, stream_mode="updates"):
            state = dict(initial_state)
            current = self._edges.get(langgraph_graph.START)
            while current and current != langgraph_graph.END:
                updates = self._nodes[current](state)
                yield {current: updates}
                if isinstance(updates, dict):
                    state.update(updates)
                current = self._edges.get(current)

    class _StateGraph:
        def __init__(self, state_type):
            self.state_type = state_type
            self.nodes = {}
            self.edges = {}

        def add_node(self, name, fn):
            self.nodes[name] = fn

        def add_edge(self, from_node, to_node):
            self.edges[from_node] = to_node

        def compile(self):
            return _CompiledGraph(self.nodes, self.edges)

    langgraph_graph.StateGraph = _StateGraph
    langgraph.graph = langgraph_graph
    sys.modules.setdefault("langgraph", langgraph)
    sys.modules.setdefault("langgraph.graph", langgraph_graph)

    dotenv = types.ModuleType("dotenv")
    dotenv.load_dotenv = lambda *args, **kwargs: None
    sys.modules.setdefault("dotenv", dotenv)

    openai = types.ModuleType("openai")

    class _OpenAI:
        def __init__(self, *args, **kwargs):
            self.chat = types.SimpleNamespace(
                completions=types.SimpleNamespace(create=self._create)
            )

        def _create(self, *args, **kwargs):
            raise RuntimeError("OpenAI client should not be called in unit tests.")

    class _OpenAIError(Exception):
        pass

    openai.OpenAI = _OpenAI
    openai.RateLimitError = _OpenAIError
    openai.APIError = _OpenAIError
    openai.APITimeoutError = _OpenAIError
    sys.modules.setdefault("openai", openai)

    tavily = types.ModuleType("tavily")

    class _TavilyClient:
        def __init__(self, *args, **kwargs):
            pass

        def search(self, *args, **kwargs):
            raise RuntimeError("Tavily client should not be called in unit tests.")

    tavily.TavilyClient = _TavilyClient
    sys.modules.setdefault("tavily", tavily)

    requests = types.ModuleType("requests")

    class _Timeout(Exception):
        pass

    class _TooManyRedirects(Exception):
        pass

    class _HTTPError(Exception):
        def __init__(self, response=None):
            super().__init__("http error")
            self.response = response or types.SimpleNamespace(status_code=500)

    requests.exceptions = types.SimpleNamespace(
        Timeout=_Timeout,
        TooManyRedirects=_TooManyRedirects,
        HTTPError=_HTTPError,
    )

    def _get(*args, **kwargs):
        raise RuntimeError("requests.get should not be called in unit tests.")

    requests.get = _get
    sys.modules.setdefault("requests", requests)
