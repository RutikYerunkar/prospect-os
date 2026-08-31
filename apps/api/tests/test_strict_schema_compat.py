"""Phase 4 de-risking gate: every one of the four LLM operations' output
schemas must be strict-Structured-Outputs compatible — `additionalProperties:
false` and every property required, recursively, on every object node."""

from __future__ import annotations

import pytest

from groundwork.models.llm_io import (
    ObjectiveParseOutput,
    PersonalizationOutput,
    ResearchExtractionOutput,
    ScoreExplanationOutput,
)
from groundwork.providers.live.schemas import is_strict_compatible, schema_name, to_strict_json_schema

ALL_OPERATION_SCHEMAS = [
    ResearchExtractionOutput, ScoreExplanationOutput, PersonalizationOutput, ObjectiveParseOutput,
]


@pytest.mark.parametrize("model", ALL_OPERATION_SCHEMAS, ids=lambda m: m.__name__)
def test_schema_is_strict_compatible(model):
    payload = to_strict_json_schema(model)
    violations = is_strict_compatible(payload)
    assert violations == [], f"{model.__name__}: {violations}"


@pytest.mark.parametrize("model", ALL_OPERATION_SCHEMAS, ids=lambda m: m.__name__)
def test_schema_payload_shape(model):
    payload = to_strict_json_schema(model)
    assert payload["type"] == "json_schema"
    assert payload["strict"] is True
    assert payload["name"] == schema_name(model)
    assert len(payload["name"]) <= 64


def test_schema_name_is_sanitized():
    from groundwork.providers.live.schemas import _NAME_RE

    assert _NAME_RE.sub("_", "Some Weird!! Name") == "Some_Weird___Name"
