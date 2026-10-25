"""Load writing prompts from one file per key.

``v1/prompts/<key>.yaml`` is the prompt body. The stem is the key a
``writing.v1.<key>`` ref names. A step with no prompt_ref has no file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_prompts(root: Path | None = None) -> dict[str, Any]:
    """Return each prompt key mapped to its YAML body.

    ``root`` overrides ``v1/prompts``. A non-mapping file is skipped.
    A file that cannot be read or parsed is skipped. Missing directory
    returns an empty map.
    """
    base = root or Path(__file__).resolve().parents[1] / "prompts"
    found: dict[str, Any] = {}
    if not base.is_dir():
        return found
    for path in sorted(base.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError, UnicodeDecodeError):
            continue
        if isinstance(data, dict):
            found[path.stem] = data
    return found
