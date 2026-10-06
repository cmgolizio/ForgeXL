"""Strict per-request CSV configuration, separate from dataset references."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

TEXT_OPERATORS = ("equals", "not_equals", "contains", "not_contains", "starts_with", "ends_with", "is_any_of", "is_none_of")
NUMBER_OPERATORS = ("equals", "not_equals", "gt", "gte", "lt", "lte", "between")
DATE_OPERATORS = ("on", "before", "after", "between")


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    column: str
    kind: Literal["text", "number", "date", "blank"]
    operator: str
    value: str | None = Field(default=None, max_length=10000)
    upper: str | None = Field(default=None, max_length=10000)
    values: list[str] = Field(default_factory=list, max_length=100)
    ignore_case: bool = False
    date_format: Literal["YYYY-MM-DD", "MM/DD/YYYY", "DD/MM/YYYY"] = "YYYY-MM-DD"

    @model_validator(mode="after")
    def check_operator(self) -> Condition:
        operators = {"text": TEXT_OPERATORS, "number": NUMBER_OPERATORS,
                     "date": DATE_OPERATORS, "blank": ("is_blank", "is_not_blank")}
        if self.operator not in operators[self.kind]:
            raise ValueError(f"Invalid {self.kind} operator: {self.operator}.")
        if self.kind == "blank":
            if self.value is not None or self.upper is not None or self.values:
                raise ValueError("Blank conditions accept no comparison values.")
        elif self.operator in ("is_any_of", "is_none_of"):
            if not self.values or self.value is not None or self.upper is not None:
                raise ValueError("Choose at least one text value; use only values for membership conditions.")
        else:
            if self.value is None or self.values:
                raise ValueError("A comparison value is required.")
            if (self.operator == "between") != (self.upper is not None):
                raise ValueError("Between requires two endpoints; other operators accept one.")
        if self.kind != "text" and self.ignore_case:
            raise ValueError("Ignore case applies only to text conditions.")
        if self.kind != "date" and self.date_format != "YYYY-MM-DD":
            raise ValueError("Date formats apply only to date conditions.")
        if any(len(value) > 10000 for value in self.values):
            raise ValueError("Selected text values may not exceed 10000 characters.")
        return self


class CSVOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    match: Literal["all", "any"] = "all"
    remove_duplicates: bool = False
    conditions: list[Condition] = Field(default_factory=list, max_length=50)


class ProcessCSV(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str = Field(min_length=1, max_length=64)
    action_id: str = Field(min_length=1, max_length=64)
    options: CSVOptions


class DiscardCSV(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str = Field(min_length=1, max_length=64)


class ReorderCSV(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str = Field(min_length=1, max_length=64)
    order: list[int] = Field(min_length=2, max_length=1000)
