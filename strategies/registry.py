from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Type

from core.params import StrategyMeta
from core.strategy_api import BaseStrategy


PLUGIN_DIR = Path(__file__).resolve().parent / "plugins"

_REGISTRY: dict[str, Type[BaseStrategy]] = {}
_LOADED = False
LOAD_ERRORS: list[str] = []
CUSTOM_ERRORS: list[str] = []


def register_strategy(cls: Type[BaseStrategy]) -> Type[BaseStrategy]:
    meta = getattr(cls, "meta", None)
    if not isinstance(meta, StrategyMeta):
        raise TypeError("策略类必须定义 StrategyMeta")
    _REGISTRY[meta.slug] = cls
    return cls


def _load_plugins() -> None:
    if not PLUGIN_DIR.exists():
        return
    for path in sorted(PLUGIN_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            module_name = f"studio_strategy_plugin_{path.stem}"
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError(f"无法加载 {path.name}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as exc:
            LOAD_ERRORS.append(f"{path.name}: {exc}")


def ensure_loaded() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    from strategies import builtin  # noqa: F401

    _load_plugins()


def _custom_metas() -> list[StrategyMeta]:
    from strategies.custom_store import custom_metas

    metas, errors = custom_metas()
    CUSTOM_ERRORS[:] = errors
    return metas


def list_strategies() -> list[StrategyMeta]:
    ensure_loaded()
    metas = {cls.meta.slug: cls.meta for cls in _REGISTRY.values()}
    for meta in _custom_metas():
        metas[meta.slug] = meta
    return sorted(metas.values(), key=lambda meta: meta.name)


def get_strategy_meta(slug: str) -> StrategyMeta | None:
    ensure_loaded()
    cls = _REGISTRY.get(slug)
    if cls:
        return cls.meta
    from strategies.custom import meta_from_definition
    from strategies.custom_store import get_definition

    definition = get_definition(slug)
    return meta_from_definition(definition) if definition else None


def create_strategy(slug: str) -> BaseStrategy:
    ensure_loaded()
    cls = _REGISTRY.get(slug)
    if cls:
        return cls()
    from strategies.custom import CustomRuleStrategy
    from strategies.custom_store import get_definition

    definition = get_definition(slug)
    if definition is None:
        raise KeyError(f"未注册的策略: {slug}")
    return CustomRuleStrategy(definition)


__all__ = [
    "CUSTOM_ERRORS",
    "LOAD_ERRORS",
    "create_strategy",
    "ensure_loaded",
    "get_strategy_meta",
    "list_strategies",
    "register_strategy",
]
