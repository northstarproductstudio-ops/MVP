"""Import agent: called once per file, only for what adapters and saved mappings could not
settle (unknown/ambiguous type, fields with no known mapping). No tools, no UI."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from core.domain.adapters import REGISTRY
from core.domain.variables import catalogue
from core.llm import LLMClient, LLMError, load_prompt
from core.models.imports import IMPORT_AGENT_JSON_SCHEMA


def library_payload() -> list[dict[str, Any]]:
    return [{"key": k, "name": a.name, "signature": a.signature} for k, a in REGISTRY.items()]


def catalogue_payload() -> list[dict[str, Any]]:
    return [{"name": v.name, "category": v.category, "type": v.data_type, "unit": v.unit, "tier": v.tier,
             "level": v.level} for v in catalogue().values()]


def make_import_agent(llm: LLMClient, company_id: str, session_id: str | None,
                      version: str = "v1") -> Callable[[dict[str, Any]], dict[str, Any] | None]:
    system = load_prompt("import_agent", version)

    def call(payload: dict[str, Any]) -> dict[str, Any] | None:
        body = {**payload, "file_type_library": library_payload(), "variable_catalogue": catalogue_payload()}
        try:
            res = llm.complete(role="import", company_id=company_id, session_id=session_id, prompt_version=version,
                               temperature=0.0, json_schema=IMPORT_AGENT_JSON_SCHEMA, schema_name="import_mapping",
                               messages=[{"role": "system", "content": system},
                                         {"role": "user", "content": json.dumps(body, default=str)}])
        except LLMError:
            return None
        return res.parsed if isinstance(res.parsed, dict) else None

    return call
