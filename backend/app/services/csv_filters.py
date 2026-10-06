"""Exact comparisons on temporary typed values; source strings are never changed."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from collections.abc import Callable

import polars as pl
from pydantic import ValidationError

from app.errors import InvalidRequestError
from app.models.csv_tools import CSVOptions, Condition

NUMBER_PATTERN = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
DATE_FORMATS = {"YYYY-MM-DD": (r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z", "%Y-%m-%d"),
                "MM/DD/YYYY": (r"[0-9]{2}/[0-9]{2}/[0-9]{4}\Z", "%m/%d/%Y"),
                "DD/MM/YYYY": (r"[0-9]{2}/[0-9]{2}/[0-9]{4}\Z", "%d/%m/%Y")}


def number(value: str) -> Decimal:
    if not NUMBER_PATTERN.fullmatch(value):
        raise ValueError("Use a finite number without spaces or grouping separators.")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("Invalid number.") from error
    if not parsed.is_finite():
        raise ValueError("Number must be finite.")
    return parsed


def calendar_date(value: str, format_name: str) -> date:
    pattern, format_string = DATE_FORMATS[format_name]
    if not re.fullmatch(pattern, value):
        raise ValueError(f"Use {format_name} exactly; dates are never guessed.")
    return datetime.strptime(value, format_string).date()


def typed_parser(condition: Condition) -> Callable:
    return number if condition.kind == "number" else lambda value: calendar_date(value, condition.date_format)


def validate_options(raw: dict, frame: pl.DataFrame, *, combine: bool) -> CSVOptions:
    try:
        options = CSVOptions.model_validate(raw)
    except ValidationError as error:
        raise InvalidRequestError("Invalid CSV filter options.", details={"issues": [
            {"message": item["msg"], "location": list(item["loc"])} for item in error.errors()
        ]}) from error
    if combine:
        options = options.model_copy(update={"remove_duplicates": True})
    elif not options.conditions:
        raise InvalidRequestError("Add at least one filter condition.")
    for condition in options.conditions:
        if condition.column not in frame.columns:
            raise InvalidRequestError(f"Unknown column: {condition.column}.")
        if condition.kind not in ("number", "date"):
            continue
        convert = typed_parser(condition)
        try:
            low = convert(condition.value)
            if condition.upper is not None and low > convert(condition.upper):
                raise ValueError("The lower endpoint must not exceed the upper endpoint.")
        except (ValueError, InvalidOperation, OverflowError) as error:
            raise InvalidRequestError(f"Invalid {condition.kind} comparison for {condition.column}: {error}") from error
        examples: list[str] = []
        # Validate the entire populated column, including rows another condition
        # would exclude. Blanks are allowed; they fail all typed comparisons.
        for value in frame[condition.column].unique(maintain_order=True):
            if value == "":
                continue
            try:
                convert(value)
            except (ValueError, InvalidOperation, OverflowError):
                examples.append(value[:200])
                if len(examples) == 5:
                    break
        if examples:
            raise InvalidRequestError(
                f"Column {condition.column} contains values unreadable as {condition.kind}: "
                f"{', '.join(repr(value) for value in examples)}. Correct them or choose an explicit date format.",
                details={"column": condition.column, "examples": examples, "kind": condition.kind},
            )
    return options


def condition_expression(condition: Condition) -> pl.Expr:
    column = pl.col(condition.column)
    op = condition.operator
    if condition.kind == "blank":
        return column == "" if op == "is_blank" else column != ""
    if condition.kind == "text":
        # casefold is explicit Unicode caseless comparison, including ß/SS.
        if condition.ignore_case:
            column = column.map_elements(str.casefold, return_dtype=pl.String)
        value = condition.value.casefold() if condition.ignore_case and condition.value is not None else condition.value
        value = value if value is not None else ""
        values = [item.casefold() if condition.ignore_case else item for item in condition.values]
        if op == "equals": return column == value
        if op == "not_equals": return column != value
        if op == "contains": return column.str.contains(value, literal=True)
        if op == "not_contains": return ~column.str.contains(value, literal=True)
        if op == "starts_with": return column.str.starts_with(value)
        if op == "ends_with": return column.str.ends_with(value)
        if op == "is_any_of": return column.is_in(values)
        return ~column.is_in(values)
    convert = typed_parser(condition)
    low = convert(condition.value)
    high = convert(condition.upper) if condition.upper is not None else None

    def compare(value: str) -> bool:
        # Blank values never satisfy typed conditions, including not_equals.
        if value == "": return False
        parsed = convert(value)
        if op in ("equals", "on"): return parsed == low
        if op == "not_equals": return parsed != low
        if op in ("gt", "after"): return parsed > low
        if op == "gte": return parsed >= low
        if op in ("lt", "before"): return parsed < low
        if op == "lte": return parsed <= low
        return low <= parsed <= high
    return column.map_elements(compare, return_dtype=pl.Boolean)


def apply_filters(frame: pl.DataFrame, options: CSVOptions) -> pl.DataFrame:
    if not options.conditions:
        return frame
    expressions = [condition_expression(condition) for condition in options.conditions]
    mask = pl.all_horizontal(expressions) if options.match == "all" else pl.any_horizontal(expressions)
    return frame.filter(mask)
