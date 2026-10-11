"""Evidence pack (planning-agent input) and agent output schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class PackFactor(BaseModel):
    name: str
    kind: Literal["multiplier", "additive"]
    value: float
    basis: str


class PackHistory(BaseModel):
    months: list[str]
    line: list[float]
    national: list[float]


class PackLine(BaseModel):
    line_id: str
    city: str
    platform: str
    base: float
    reference_dp: int | None = None
    factors: list[PackFactor]
    signals: dict[str, Any]
    history: PackHistory


class EvidencePack(BaseModel):
    sku: str
    name: str | None
    plan_category: str | None
    abc: str | None
    sb_class: str | None
    target_month: str
    as_of: str
    horizon_months: int
    lines: list[PackLine]
    flags: list[str] = Field(default_factory=list)


class AppliedFactor(BaseModel):
    name: str
    value: float
    reason: str | None = None


class AgentLine(BaseModel):
    line_id: str
    final_dp: float
    applied_factors: list[AppliedFactor]
    reason: str
    confidence: Literal["high", "medium", "low"]
    flag_for_review: bool


class AgentOutput(BaseModel):
    lines: list[AgentLine]


AGENT_OUTPUT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["lines"],
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["line_id", "final_dp", "applied_factors", "reason", "confidence",
                             "flag_for_review"],
                "properties": {
                    "line_id": {"type": "string"},
                    "final_dp": {"type": "number"},
                    "applied_factors": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["name", "value", "reason"],
                            "properties": {
                                "name": {"type": "string"},
                                "value": {"type": "number"},
                                "reason": {"type": ["string", "null"]},
                            },
                        },
                    },
                    "reason": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "flag_for_review": {"type": "boolean"},
                },
            },
        }
    },
}


# --------------------------------------------------------------------------- prompt v2 (batches)
# One call per category batch (Gaps 3–5). Category-wide effects are declared once in
# ``category_factors``; a line uses one by listing it in ``applied_factors`` with
# ``value: null`` (take the category value), or overrides it with its own value and reason.

class CategoryFactor(BaseModel):
    name: str
    value: float
    reason: str


class BatchAppliedFactor(BaseModel):
    name: str
    value: float | None = None
    reason: str | None = None


class BatchLine(BaseModel):
    line_id: str
    final_dp: float
    applied_factors: list[BatchAppliedFactor]
    reason: str
    confidence: Literal["high", "medium", "low"]
    flag_for_review: bool


class BatchOutput(BaseModel):
    category: str | None = None
    category_factors: list[CategoryFactor] = Field(default_factory=list)
    lines: list[BatchLine]


AGENT_OUTPUT_JSON_SCHEMA_V2: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["category", "category_factors", "lines"],
    "properties": {
        "category": {"type": ["string", "null"]},
        "category_factors": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "value", "reason"],
                "properties": {
                    "name": {"type": "string"},
                    "value": {"type": "number"},
                    "reason": {"type": "string"},
                },
            },
        },
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["line_id", "final_dp", "applied_factors", "reason", "confidence",
                             "flag_for_review"],
                "properties": {
                    "line_id": {"type": "string"},
                    "final_dp": {"type": "number"},
                    "applied_factors": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["name", "value", "reason"],
                            "properties": {
                                "name": {"type": "string"},
                                "value": {"type": ["number", "null"]},
                                "reason": {"type": ["string", "null"]},
                            },
                        },
                    },
                    "reason": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "flag_for_review": {"type": "boolean"},
                },
            },
        },
    },
}
