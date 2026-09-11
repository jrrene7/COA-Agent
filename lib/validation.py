from typing import Type


def validate_schema(data: dict, schema: dict, error_cls: Type[Exception], agent_name: str) -> dict:
    """Fill missing keys with safe defaults; raise on a type mismatch that would
    break downstream rendering (e.g. ReportWriter iterating a string as a list).

    `schema` maps key -> (expected_type_or_tuple, default_value).
    """
    for key, (expected_type, default) in schema.items():
        if key not in data or data[key] is None:
            data[key] = default
            continue
        if not isinstance(data[key], expected_type):
            raise error_cls(
                f"{agent_name}: field '{key}' expected {expected_type}, "
                f"got {type(data[key]).__name__}."
            )
    return data
