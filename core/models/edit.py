"""Direct-edit schema (Master Build Spec, Direct edits and report versions)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class EditTarget(BaseModel):
    sku_code: str | None = None
    plan_category: str | None = None
    city: str | None = None
    platform: str | None = None

    def is_empty(self) -> bool:
        return not any([self.sku_code, self.plan_category, self.city, self.platform])


class Edit(BaseModel):
    target: EditTarget
    op: Literal["pct", "set"]
    value: float
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _check(self) -> Edit:
        if self.target.is_empty():
            raise ValueError("at least one target field is required")
        if self.op == "pct" and not (-90 <= self.value <= 300):
            raise ValueError("pct edits must be between -90 and +300")
        if self.op == "set":
            if self.value < 0 or self.value != int(self.value):
                raise ValueError("set edits take a whole, non-negative number of units")
            if not (self.target.sku_code and self.target.city and self.target.platform):
                raise ValueError("set edits need a single line: sku_code, city and platform")
        return self

    def describe(self) -> str:
        t = self.target
        parts = [p for p in (t.sku_code, t.plan_category, t.city, t.platform) if p]
        what = " / ".join(parts)
        if self.op == "pct":
            return f"{what} {self.value:+g}%"
        return f"{what} set to {int(self.value)}"
