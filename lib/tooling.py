import inspect
from typing import Callable, Any, Dict, Optional


class Tool:
    def __init__(self, fn: Callable, name: str, description: str, parameters: dict):
        self.fn = fn
        self.name = name
        self.description = description
        self.parameters = parameters

    def __call__(self, **kwargs) -> Any:
        return self.fn(**kwargs)

    def to_openai_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def tool(fn: Callable) -> Tool:
    """Decorator that converts a function into a Tool with an auto-extracted OpenAI schema."""
    sig = inspect.signature(fn)
    doc = inspect.getdoc(fn) or ""

    _PY_TO_JSON = {
        int: "integer",
        float: "number",
        bool: "boolean",
        str: "string",
    }

    properties: Dict[str, dict] = {}
    required: list = []

    for param_name, param in sig.parameters.items():
        if param_name == "self":
            continue
        annotation = param.annotation
        json_type = _PY_TO_JSON.get(annotation, "string")
        properties[param_name] = {"type": json_type}
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    parameters = {
        "type": "object",
        "properties": properties,
        "required": required,
    }

    return Tool(fn=fn, name=fn.__name__, description=doc, parameters=parameters)
