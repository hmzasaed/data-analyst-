"""The tool registry.

Rule: the agent may only call tools that are registered here. Each entry declares a
strict input schema, an output contract, resource limits, whether it mutates data
and whether it needs human approval. Anything not in the registry is unavailable
by construction rather than by policy.

Parameter validation is a deliberately small, closed vocabulary of JSON-schema-like
types. It is enough to reject hallucinated parameters and wrong column names
before any work happens, and it keeps every tool's contract readable in the docs.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from insightflow_contracts import PlanRejectedError, ToolExecutionError, ToolSpec

from insightflow_analytics.context import AnalysisContext, ToolOutcome

ToolHandler = Callable[[AnalysisContext, dict[str, Any]], ToolOutcome]

#: Parameter kinds understood by the validator.
TYPES = frozenset(
    {
        "string",
        "integer",
        "number",
        "boolean",
        "enum",
        "string_list",
        "column",
        "numeric_column",
        "temporal_column",
        "category_column",
        "json_object",
    }
)

#: Upper bound on a `json_object` argument, so a plan cannot smuggle a payload.
MAX_JSON_OBJECT_BYTES = 4096

@dataclass(frozen=True)
class ParameterSpec:
    name: str
    type: str
    required: bool = False
    default: Any = None
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    description: str = ""

    def json_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {
            "type": self.type,
            "required": self.required,
            "description": self.description,
        }
        if self.choices:
            schema["enum"] = list(self.choices)
        if self.default is not None:
            schema["default"] = self.default
        if self.minimum is not None:
            schema["minimum"] = self.minimum
        if self.maximum is not None:
            schema["maximum"] = self.maximum
        return schema

@dataclass(frozen=True)
class RegisteredTool:
    spec: ToolSpec
    parameters: tuple[ParameterSpec, ...]
    handler: ToolHandler
    outputs: tuple[str, ...] = field(default=("metric",))

    def signature(self) -> str:
        parts = []
        for parameter in self.parameters:
            flag = "" if parameter.required else "?"
            parts.append(f"{parameter.name}: {parameter.type}{flag}")
        return f"{self.spec.name}({', '.join(parts)})"

class ToolRegistry:
    """Append-only registry of approved analyses."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    # ------------------------------------------------------------ registration
    def register(
        self,
        *,
        name: str,
        version: str,
        analysis_type: str,
        summary: str,
        parameters: tuple[ParameterSpec, ...],
        handler: ToolHandler,
        outputs: tuple[str, ...] = ("metric",),
        max_rows: int = 5_000_000,
        timeout_seconds: int = 120,
        mutates_data: bool = False,
        requires_user_approval: bool = False,
        requires_sandbox: bool = False,
        tags: tuple[str, ...] = (),
    ) -> RegisteredTool:
        if name in self._tools:
            raise ValueError(f"tool '{name}' is already registered")
        spec = ToolSpec(
            name=name,
            version=version,
            analysis_type=analysis_type,
            summary=summary,
            input_schema={parameter.name: parameter.json_schema() for parameter in parameters},
            allowed_dataset_refs=["dataset_id", "snapshot.version"],
            max_rows=max_rows,
            timeout_seconds=timeout_seconds,
            mutates_data=mutates_data,
            requires_user_approval=requires_user_approval,
            requires_sandbox=requires_sandbox,
            deterministic=True,
            tags=list(tags),
        )
        tool = RegisteredTool(spec=spec, parameters=parameters, handler=handler, outputs=outputs)
        self._tools[name] = tool
        return tool

    # ------------------------------------------------------------------ lookup
    def get(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def all(self) -> list[RegisteredTool]:
        return [self._tools[name] for name in self.names()]

    def by_analysis_type(self, analysis_type: str) -> list[RegisteredTool]:
        return [tool for tool in self.all() if tool.spec.analysis_type == analysis_type]

    def catalogue(self) -> list[ToolSpec]:
        """The tool list handed to the planner, and rendered in docs."""
        return [tool.spec for tool in self.all()]

    # -------------------------------------------------------------- validation
    def validate_parameters(
        self,
        name: str,
        parameters: dict[str, Any],
        *,
        context: AnalysisContext | None = None,
    ) -> dict[str, Any]:
        """Reject unknown, missing or ill-typed parameters before execution.

        Raises `PlanRejectedError`, which the orchestrator treats as a user-facing
        "the plan could not be validated" rather than a transient failure.
        """
        tool = self._tools.get(name)
        if tool is None:
            raise PlanRejectedError(
                f"unknown tool '{name}'",
                violations=[f"'{name}' is not a registered analysis"],
                available=self.names(),
            )

        violations: list[str] = []
        declared = {parameter.name: parameter for parameter in tool.parameters}
        unknown = sorted(set(parameters) - set(declared))
        if unknown:
            violations.append(f"unknown parameters for {name}: {unknown}")

        cleaned: dict[str, Any] = {}
        for parameter in tool.parameters:
            if parameter.name not in parameters:
                if parameter.required:
                    violations.append(f"{name}.{parameter.name} is required")
                elif parameter.default is not None:
                    cleaned[parameter.name] = parameter.default
                continue
            value = parameters[parameter.name]
            try:
                cleaned[parameter.name] = _coerce(parameter, value, context)
            except ValueError as exc:
                violations.append(f"{name}.{parameter.name}: {exc}")

        if violations:
            raise PlanRejectedError(
                f"parameters for '{name}' failed validation",
                violations=violations,
                signature=tool.signature(),
            )
        return cleaned

def _coerce(parameter: ParameterSpec, value: Any, context: AnalysisContext | None) -> Any:
    kind = parameter.type
    if kind in {"string", "column"}:
        coerced = _require_str(value)
        if kind == "column":
            # A `column` parameter must name a column that exists; a plain `string`
            # is free text (a unit label, a chart title) and is not resolved.
            _check_column(kind, coerced, context)
        return coerced
    if kind == "enum":
        if value not in parameter.choices:
            raise ValueError(f"expected one of {list(parameter.choices)}, got {value!r}")
        return value
    if kind == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in {"true", "false"}:
            return value.lower() == "true"
        raise ValueError("expected a boolean")
    if kind == "integer":
        if isinstance(value, bool) or not isinstance(value, (int, str, float)):
            raise ValueError("expected an integer")
        try:
            coerced_number = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("expected an integer") from exc
        return _bounds(parameter, coerced_number)
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError("expected a number")
        try:
            coerced_number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("expected a number") from exc
        return _bounds(parameter, coerced_number)
    if kind == "string_list":
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError("expected a list of strings")
        if context is not None:
            for item in value:
                _check_column("column", item, context)
        return list(value)
    if kind == "json_object":
        return _coerce_json_object(value)
    if kind in {"numeric_column", "temporal_column", "category_column"}:
        coerced = _require_str(value)
        _check_column(kind, coerced, context)
        return coerced
    raise ValueError(f"unknown parameter type '{kind}'")

def _check_column(kind: str, name: str, context: AnalysisContext | None) -> None:
    """Validate a column reference, reporting problems as plan violations.

    `AnalysisContext` raises `ToolExecutionError` for a missing or wrongly-typed
    column, which is the right signal at execution time. During plan validation the
    same problem is a bad plan, so it is translated here rather than escaping as a
    tool failure.
    """
    if context is None:
        return
    try:
        if kind in {"numeric_column"}:
            context.numeric_column(name)
        elif kind in {"temporal_column"}:
            context.temporal_column(name)
        elif kind in {"category_column"}:
            context.category_column(name)
        else:
            context.require_columns(name)
    except ToolExecutionError as exc:
        raise ValueError(exc.message) from exc

def _require_str(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("expected a column name")
    return value

def _coerce_json_object(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        # Deliberately a ValueError, not a TypeError: the caller collects this
        # into user-facing plan violations rather than treating it as a bug.
        raise ValueError("expected a JSON object")  # noqa: TRY004
    encoded = json.dumps(value, default=str)
    if len(encoded.encode("utf-8")) > MAX_JSON_OBJECT_BYTES:
        raise ValueError(f"object exceeds {MAX_JSON_OBJECT_BYTES} bytes")
    nested: list[str] = []
    for key in value:
        if not isinstance(key, str):
            nested.append("keys must be strings")
        elif key.startswith("__"):
            nested.append(f"key '{key}' looks like a Python dunder attribute")
    if nested:
        raise ValueError("; ".join(nested))
    return dict(value)

def _require_str(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("expected a column name")
    return value

def _bounds(parameter: ParameterSpec, value: float) -> float:
    if parameter.minimum is not None and value < parameter.minimum:
        raise ValueError(f"must be >= {parameter.minimum}")
    if parameter.maximum is not None and value > parameter.maximum:
        raise ValueError(f"must be <= {parameter.maximum}")
    return value
