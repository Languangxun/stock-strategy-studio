from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Type

from core.model_api import BaseModel
from core.params import ModelMeta


ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"
PLUGIN_DIR = Path(__file__).resolve().parent / "plugins"

_REGISTRY: dict[str, Type[BaseModel]] = {}
_LOADED = False
LOAD_ERRORS: list[str] = []


def register_model(cls: Type[BaseModel]) -> Type[BaseModel]:
    meta = getattr(cls, "meta", None)
    if not isinstance(meta, ModelMeta):
        raise TypeError("模型类必须定义 ModelMeta")
    _REGISTRY[meta.slug] = cls
    return cls


def _load_plugins() -> None:
    if not PLUGIN_DIR.exists():
        return
    for path in sorted(PLUGIN_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            module_name = f"studio_model_plugin_{path.stem}"
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
    from models import sm701_model  # noqa: F401

    _load_plugins()


def list_models() -> list[ModelMeta]:
    ensure_loaded()
    return sorted((cls.meta for cls in _REGISTRY.values()), key=lambda meta: meta.name)


def get_model_meta(slug: str) -> ModelMeta | None:
    ensure_loaded()
    cls = _REGISTRY.get(slug)
    return cls.meta if cls else None


def create_model(slug: str) -> BaseModel:
    ensure_loaded()
    cls = _REGISTRY.get(slug)
    if cls is None:
        raise KeyError(f"未注册的模型: {slug}")
    return cls(ARTIFACT_DIR / slug)


__all__ = [
    "ARTIFACT_DIR",
    "LOAD_ERRORS",
    "create_model",
    "ensure_loaded",
    "get_model_meta",
    "list_models",
    "register_model",
]
