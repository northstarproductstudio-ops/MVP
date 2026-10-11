"""Import-agent output and mapping payloads (MVP-04)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

MappingState = Literal["recognised", "proposed", "new_category", "needs_decision", "not_stored",
                       "confirmed", "custom"]


class NewVariable(BaseModel):
    name: str
    category: str
    data_type: str = "text"
    display_name: str | None = None  # Gap 11: plain words for planners


class ProposedField(BaseModel):
    sheet: str
    source_field: str
    variable: str | None = None
    tier: int | None = None
    category: str | None = None
    state: MappingState = "needs_decision"
    confidence: float = 0.0
    new_variable: NewVariable | None = None
    sample_values: list[Any] = Field(default_factory=list)
    inferred_type: str | None = None
    blank_pct: float | None = None
    note: str | None = None


class SheetChoice(BaseModel):
    sheet: str
    header_row: int
    use: bool = True


class ImportProposal(BaseModel):
    file_type: str
    type_confidence: float
    type_source: Literal["adapter", "agent", "user", "saved"] = "adapter"
    ask_user: bool = False
    question: str | None = None
    sheets: list[SheetChoice] = Field(default_factory=list)
    fields: list[ProposedField] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


IMPORT_AGENT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["file_type", "type_confidence", "ask_user", "question", "sheets", "fields"],
    "properties": {
        "file_type": {"type": "string"},
        "type_confidence": {"type": "number"},
        "ask_user": {"type": "boolean"},
        "question": {"type": ["string", "null"]},
        "sheets": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["sheet", "header_row", "use"],
            "properties": {"sheet": {"type": "string"}, "header_row": {"type": "integer"},
                           "use": {"type": "boolean"}}}},
        "fields": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["sheet", "source_field", "variable", "tier", "category", "state",
                         "confidence", "new_variable"],
            "properties": {
                "sheet": {"type": "string"},
                "source_field": {"type": "string"},
                "variable": {"type": ["string", "null"]},
                "tier": {"type": ["integer", "null"]},
                "category": {"type": ["string", "null"]},
                "state": {"type": "string",
                          "enum": ["proposed", "new_category", "needs_decision", "not_stored"]},
                "confidence": {"type": "number"},
                "new_variable": {"anyOf": [{"type": "null"}, {
                    "type": "object", "additionalProperties": False,
                    "required": ["name", "category", "data_type", "display_name"],
                    "properties": {"name": {"type": "string"}, "category": {"type": "string"},
                                   "data_type": {"type": "string"}, "display_name": {"type": "string"}}}]},
            }}},
    },
}


class SavedField(BaseModel):
    sheet: str
    source_field: str
    variable: str | None = None
    category: str | None = None
    state: MappingState = "confirmed"
    new_variable: NewVariable | None = None


class MappingSave(BaseModel):
    fields: list[SavedField]
    note: str | None = None
