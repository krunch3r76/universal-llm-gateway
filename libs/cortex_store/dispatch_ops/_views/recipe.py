"""Load and validate package-data view profile recipes."""

from __future__ import annotations

import importlib.resources
import json
import re
from typing import Any

_RECIPE_ID_RE = re.compile(r"^recipe:(?P<profile>[a-z_]+)/v(?P<version>\d+)$")
_KNOWN_PROFILES = frozenset({"matter_charter", "matter_doctrine", "matter_index"})
_REQUIRED_SECTION_KEYS = frozenset(
    {"section_id", "title", "order", "core", "derivation", "watched_set"}
)


class ViewRecipeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def parse_recipe_id(recipe_id: str) -> tuple[str, int]:
    match = _RECIPE_ID_RE.match(recipe_id.strip())
    if not match:
        raise ViewRecipeError(
            "unknown_view_profile",
            f"Invalid derivation_recipe id {recipe_id!r}; expected recipe:{{profile}}/v{{n}}",
        )
    return match.group("profile"), int(match.group("version"))


def load_recipe(profile: str, version: int = 1) -> dict[str, Any]:
    if profile not in _KNOWN_PROFILES:
        raise ViewRecipeError("unknown_view_profile", f"Unknown view profile {profile!r}")
    filename = f"{profile}.v{version}.json"
    pkg = importlib.resources.files("cortex_store.view_recipes")
    try:
        raw = pkg.joinpath(filename).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ViewRecipeError(
            "unknown_view_profile",
            f"Recipe file missing for profile={profile!r} version={version}",
        ) from exc
    recipe = json.loads(raw)
    validate_recipe(recipe, expected_profile=profile, expected_version=version)
    return recipe


def validate_recipe(
    recipe: dict[str, Any],
    *,
    expected_profile: str | None = None,
    expected_version: int | None = None,
) -> None:
    if not isinstance(recipe, dict):
        raise ViewRecipeError("recipe_profile_mismatch", "Recipe must be a JSON object")
    profile = recipe.get("profile")
    version = recipe.get("version")
    if expected_profile and profile != expected_profile:
        raise ViewRecipeError(
            "recipe_profile_mismatch",
            f"Recipe profile {profile!r} != expected {expected_profile!r}",
        )
    if expected_version is not None and version != expected_version:
        raise ViewRecipeError(
            "recipe_profile_mismatch",
            f"Recipe version {version!r} != expected {expected_version}",
        )
    sections = recipe.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ViewRecipeError("recipe_profile_mismatch", "Recipe sections must be a non-empty list")
    for section in sections:
        if not isinstance(section, dict):
            raise ViewRecipeError("recipe_profile_mismatch", "Each section must be an object")
        missing = _REQUIRED_SECTION_KEYS - section.keys()
        if missing:
            raise ViewRecipeError(
                "recipe_profile_mismatch",
                f"Section missing keys: {sorted(missing)}",
            )


__all__ = [
    "ViewRecipeError",
    "load_recipe",
    "parse_recipe_id",
    "validate_recipe",
]
