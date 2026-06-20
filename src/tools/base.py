#!/usr/bin/python3

from typing import Any


# JSON-schema type name -> acceptable Python types for lightweight validation
_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def validate_tool_args(parameters: dict[str, Any], args: dict[str, Any]) -> str | None:
    """
    This function checks tool-call arguments against a tool's JSON-schema parameters,
    returning a human-readable error string (for the model to self-correct) or None
    when the arguments are acceptable. Validation is intentionally lightweight: it
    enforces required keys and basic scalar/container types without deep schema rules.
    """
    if not isinstance(args, dict):
        return f"Error: tool arguments must be a JSON object, got {type(args).__name__}."

    properties: dict[str, Any] = parameters.get("properties", {}) or {}
    required: list[str] = parameters.get("required", []) or []

    missing: list[str] = [
        key for key in required
        if key not in args or args[key] is None or (isinstance(args[key], str) and args[key] == "")
    ]
    if missing:
        return f"Error: missing required parameter(s): {', '.join(missing)}."

    for key, value in args.items():
        spec: Any = properties.get(key)
        if not isinstance(spec, dict):
            continue
        expected: Any = spec.get("type")
        if not isinstance(expected, str) or expected not in _TYPE_MAP:
            continue
        if value is None:
            continue
        # bool is a subclass of int — reject it for numeric types to avoid silent coercion
        allowed: tuple[type, ...] = _TYPE_MAP[expected]
        if expected in ("integer", "number") and isinstance(value, bool):
            return f"Error: parameter '{key}' must be of type {expected}, got boolean."
        if not isinstance(value, allowed):
            return f"Error: parameter '{key}' must be of type {expected}, got {type(value).__name__}."

    return None


class BaseTool:

    # Per-room tracker for read-before-write enforcement on core files
    # Keyed by room_id to prevent cross-room authorization bypass
    _read_files: dict[str, set[str]] = {}

    def __init__(self, name: str, description: str, parameters: dict[str, Any]) -> None:
        """
        This is the BaseTool class which all tools inherit from.
        """
        # Tool name (used by the LLM to call this tool)
        self.name: str = name

        # Human-readable description of what this tool does
        self.description: str = description

        # JSON Schema describing the tool's parameters
        self.parameters: dict[str, Any] = parameters

    def execute(self, **kwargs: Any) -> str:
        """
        This function executes the tool and returns a string result.
        """
        raise NotImplementedError

    def schema(self) -> dict[str, Any]:
        """
        This function returns the tool definition in OpenAI-compatible format.
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
            }
        }
