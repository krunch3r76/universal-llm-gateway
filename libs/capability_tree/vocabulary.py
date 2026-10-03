"""Per-root category vocabulary, unioned at registry load."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from universal_logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class CategoryVocabulary:
    """Frozen set of legal category names and their descriptions."""

    names: frozenset[str]
    descriptions: dict[str, str]

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self.names

    @classmethod
    def empty(cls) -> CategoryVocabulary:
        return cls(names=frozenset(), descriptions={})

    @classmethod
    def load(cls, paths: list[Path]) -> CategoryVocabulary:
        """Union names from each ``categories.yaml``. A bad file adds nothing."""
        names: set[str] = set()
        descriptions: dict[str, str] = {}
        for path in paths:
            loaded = _read_one(path)
            if loaded is None:
                continue
            for name, description in loaded.items():
                names.add(name)
                descriptions.setdefault(name, description)
        return cls(names=frozenset(names), descriptions=descriptions)

    def to_state(self) -> dict[str, object]:
        return {
            "names": sorted(self.names),
            "descriptions": dict(self.descriptions),
        }

    @classmethod
    def from_state(cls, raw: object) -> CategoryVocabulary:
        if not isinstance(raw, dict):
            raise TypeError("category vocabulary state is not an object")
        names_raw = raw.get("names")
        descriptions_raw = raw.get("descriptions") or {}
        if not isinstance(names_raw, list) or not isinstance(descriptions_raw, dict):
            raise TypeError("category vocabulary state shape is invalid")
        names = frozenset(str(name) for name in names_raw)
        descriptions = {str(key): str(value) for key, value in descriptions_raw.items()}
        return cls(names=names, descriptions=descriptions)


def _read_one(path: Path) -> dict[str, str] | None:
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.error("Failed to parse category vocabulary %s: %s", path, exc)
        return None
    categories = data.get("categories") if isinstance(data, dict) else None
    if not isinstance(categories, dict):
        logger.error("Category vocabulary %s is not a name→description mapping", path)
        return None
    out: dict[str, str] = {}
    for name, body in categories.items():
        if not isinstance(name, str) or not name:
            logger.error("Category vocabulary %s has a non-string name", path)
            return None
        description = ""
        if isinstance(body, dict):
            description = str(body.get("description") or "")
        elif body is not None:
            logger.error("Category vocabulary %s entry %s is not a mapping", path, name)
            return None
        out[name] = description
    return out
