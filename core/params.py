from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class ParamSpec:
    key: str
    label: str
    kind: str = "float"
    default: Any = None
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    choices: tuple[Any, ...] = ()
    help: str = ""

    def normalize(self, value: Any) -> Any:
        if self.kind == "bool":
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on", "是")
            return bool(value)
        if self.kind == "int":
            return int(float(value))
        if self.kind == "float":
            return float(value)
        if self.kind == "choice":
            if value not in self.choices:
                return self.default if self.default is not None else self.choices[0]
            return value
        return str(value)


def normalize_params(specs: Iterable[ParamSpec], values: dict[str, Any] | None) -> dict[str, Any]:
    values = dict(values or {})
    out: dict[str, Any] = {}
    for spec in specs:
        raw = values.get(spec.key, spec.default)
        if raw is None:
            out[spec.key] = None
            continue
        try:
            out[spec.key] = spec.normalize(raw)
        except (TypeError, ValueError):
            out[spec.key] = spec.default
    return out


@dataclass
class StrategyMeta:
    slug: str
    name: str
    description: str = ""
    params: tuple[ParamSpec, ...] = field(default_factory=tuple)
    requires_model: bool = False
    default_model: str | None = None
    min_bars: int = 65

    def defaults(self) -> dict[str, Any]:
        return {spec.key: spec.default for spec in self.params}


@dataclass
class ModelMeta:
    slug: str
    name: str
    version: str = "1.0"
    description: str = ""
    adjust: str = "qfq"
    params: tuple[ParamSpec, ...] = field(default_factory=tuple)

    def defaults(self) -> dict[str, Any]:
        return {spec.key: spec.default for spec in self.params}
