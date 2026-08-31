"""Pydantic -> OpenAI strict-Structured-Outputs JSON Schema (Checkpoint G
Phase 4 de-risking spike).

OpenAI's `strict: true` json_schema mode requires, recursively, on every
object: `additionalProperties: false` and *every* property name listed in
`required` (optionality is expressed by the property's own type accepting
`null`, not by omission from `required`). Pydantic v2's `model_json_schema()`
already makes an `Optional[X] = None` field a `{"anyOf": [...,{"type":
"null"}]}` union — it just doesn't force such fields into `required` or set
`additionalProperties` on nested objects, so this module does exactly those
two mechanical transforms and nothing else. It never weakens a domain model
to fit the wire format (§ Phase 4: "DO NOT weaken domain models simply to
satisfy provider schema constraints") — `models/llm_io.py` stays exactly as
`domain`/`engine` need it; this only reshapes the schema *view* sent over
the wire.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]")


def schema_name(model: type[BaseModel]) -> str:
    """OpenAI requires `^[a-zA-Z0-9_-]+$`, max 64 chars."""
    return _NAME_RE.sub("_", model.__name__)[:64]


def _tighten(node: Any) -> None:
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            node["additionalProperties"] = False
            props = node.get("properties", {})
            node["required"] = list(props.keys())
        for value in node.values():
            _tighten(value)
    elif isinstance(node, list):
        for item in node:
            _tighten(item)


def to_strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Full strict-mode `json_schema` payload for the Responses API's
    `text.format`, ready to pass through as-is:
    `{"type": "json_schema", "name": ..., "schema": ..., "strict": True}`.
    """
    raw = model.model_json_schema()
    _tighten(raw)
    return {
        "type": "json_schema",
        "name": schema_name(model),
        "schema": raw,
        "strict": True,
    }


def is_strict_compatible(schema: dict[str, Any]) -> list[str]:
    """Returns a list of violations (empty = compatible). Used by the
    compatibility test — walks every object node and checks the two
    invariants strict mode requires, plus flags any bare/unconstrained
    free-form dict (`type: object` with no `properties` and no
    `additionalProperties: false`), which strict mode also rejects.
    """
    violations: list[str] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                if node.get("additionalProperties") is not False:
                    violations.append(f"{path}: additionalProperties must be false")
                props = node.get("properties", {})
                required = set(node.get("required", []))
                missing = set(props.keys()) - required
                if missing:
                    violations.append(f"{path}: properties not required: {sorted(missing)}")
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")

    walk(schema.get("schema", schema), "$")
    return violations
