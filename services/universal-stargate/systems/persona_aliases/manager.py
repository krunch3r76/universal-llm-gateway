from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from universal_hot_reload import read_text_preserving_timestamps
from universal_logging import get_logger

from .types import PersonaAlias

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PersonaAliasManager:
    """
    Startup-loaded registry of user-local persona aliases.

    Source of truth: config_dir/persona_aliases.yaml (usually under ~/.gateway/).

    This is intentionally separate from model catalog synthetic IDs and from
    pipeline-local models.yaml aliases:
    - Catalog IDs are global and reflect loadable engine variants.
    - Pipelines' models.yaml aliases are pipeline-internal.
    - Persona aliases are *ingress conveniences* that rewrite requests onto the
      normal chat path (streaming preserved).
    """

    config_path: Path
    _aliases: dict[str, PersonaAlias]

    @classmethod
    def load_from_config_dir(cls, config_dir: Path) -> PersonaAliasManager:
        config_path = config_dir / "persona_aliases.yaml"
        aliases = cls._load_aliases(config_path)
        logger.info(
            "Loaded %d persona alias(es) from %s",
            len(aliases),
            config_path,
        )
        return cls(config_path=config_path, _aliases=aliases)

    def list_aliases(self) -> list[PersonaAlias]:
        return [self._aliases[k] for k in sorted(self._aliases.keys())]

    def get(self, alias_id: str) -> PersonaAlias | None:
        return self._aliases.get(alias_id)

    @staticmethod
    def _load_aliases(config_path: Path) -> dict[str, PersonaAlias]:
        if not config_path.exists():
            return {}

        content = read_text_preserving_timestamps(config_path)
        parsed = yaml.safe_load(content)
        if parsed is None:
            return {}
        if not isinstance(parsed, dict):
            raise TypeError(
                f"Invalid persona_aliases.yaml root type: expected mapping, "
                f"got {type(parsed).__name__}"
            )

        raw_aliases = parsed.get("aliases", {})
        if raw_aliases is None:
            return {}
        if not isinstance(raw_aliases, dict):
            raise TypeError(
                "Invalid persona_aliases.yaml: 'aliases' must be a mapping, "
                f"got {type(raw_aliases).__name__}"
            )

        result: dict[str, PersonaAlias] = {}
        for alias_id, raw in raw_aliases.items():
            if not isinstance(alias_id, str) or not alias_id.strip():
                raise ValueError("Persona alias keys must be non-empty strings")
            if not isinstance(raw, dict):
                raise TypeError(
                    f"Invalid persona alias '{alias_id}': expected mapping, "
                    f"got {type(raw).__name__}"
                )

            backing_model = raw.get("model")
            if not isinstance(backing_model, str) or not backing_model.strip():
                raise ValueError(
                    f"Invalid persona alias '{alias_id}': missing/invalid 'model'"
                )

            system_prompt = raw.get("system_prompt")
            if system_prompt is not None and not isinstance(system_prompt, str):
                raise TypeError(
                    f"Invalid persona alias '{alias_id}': "
                    "'system_prompt' must be a string"
                )
            if isinstance(system_prompt, str) and not system_prompt.strip():
                system_prompt = None

            params = raw.get("params") or {}
            if not isinstance(params, dict):
                raise TypeError(
                    f"Invalid persona alias '{alias_id}': 'params' must be a mapping"
                )

            # Only allow scalar JSON/YAML-ish values for request param fill.
            safe_params: dict[str, Any] = {}
            for k, v in params.items():
                if not isinstance(k, str) or not k.strip():
                    raise ValueError(
                        f"Invalid persona alias '{alias_id}': "
                        "param keys must be strings"
                    )
                safe_params[k] = v

            result[alias_id] = PersonaAlias(
                alias_id=alias_id,
                backing_model=backing_model.strip(),
                system_prompt=system_prompt,
                params=safe_params,
            )

        return result
