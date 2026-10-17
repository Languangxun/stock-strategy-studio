from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from strategies.custom import meta_from_definition, normalize_definition


CUSTOM_DIR = Path(__file__).resolve().parent / "custom"
_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{3,80}$")


def ensure_dir() -> Path:
    CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    return CUSTOM_DIR


def _new_slug() -> str:
    ensure_dir()
    base = "custom_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = base
    suffix = 1
    while (CUSTOM_DIR / f"{slug}.json").exists():
        suffix += 1
        slug = f"{base}_{suffix}"
    return slug


def list_definitions() -> tuple[list[dict[str, Any]], list[str]]:
    ensure_dir()
    definitions: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in sorted(CUSTOM_DIR.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not raw.get("slug"):
                raw["slug"] = path.stem
            definition = normalize_definition(raw)
            if not _SLUG_RE.match(definition["slug"]):
                raise ValueError("slug 无效")
            definitions.append(definition)
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")
    definitions.sort(key=lambda item: (item.get("name", ""), item.get("slug", "")))
    return definitions, errors


def save_definition(raw: dict[str, Any], as_new: bool = False) -> dict[str, Any]:
    definition = normalize_definition(raw)
    if as_new or not definition.get("slug"):
        definition["slug"] = _new_slug()
        definition["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not _SLUG_RE.match(definition["slug"]):
        raise ValueError("策略内部 ID 无效")
    definitions, _errors = list_definitions()
    duplicate = next(
        (
            item for item in definitions
            if item.get("slug") != definition["slug"]
            and str(item.get("name", "")).strip().casefold() == definition["name"].strip().casefold()
        ),
        None,
    )
    if duplicate is not None:
        raise ValueError(f"已存在同名自定义策略：{duplicate['name']}")
    definition["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ensure_dir()
    target = CUSTOM_DIR / f"{definition['slug']}.json"
    temp = target.with_suffix(".json.tmp")
    temp.write_text(json.dumps(definition, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(target)
    return definition


def delete_definition(slug: str) -> None:
    if not _SLUG_RE.match(str(slug)):
        raise ValueError("策略 ID 无效")
    target = CUSTOM_DIR / f"{slug}.json"
    if target.exists():
        target.unlink()


def get_definition(slug: str) -> dict[str, Any] | None:
    if not _SLUG_RE.match(str(slug)):
        return None
    target = CUSTOM_DIR / f"{slug}.json"
    if not target.exists():
        return None
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        raw.setdefault("slug", slug)
        return normalize_definition(raw)
    except Exception:
        return None


def custom_metas() -> tuple[list[Any], list[str]]:
    definitions, errors = list_definitions()
    return [meta_from_definition(definition) for definition in definitions], errors
