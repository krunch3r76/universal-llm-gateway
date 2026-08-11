"""Per-slug import-grammar blind measurements for CONSUMERS mint trust.

The regex BFS in ``consumer_import_verify`` structurally misses three classes.
This module **measures** whether each class is applicable under a slug's
``path_prefix`` tree — presence, not proof of a missed edge. Used to decide
whether a ``contradicted`` omit is earned (all zero) or must escalate.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

BlindClass = str  # from_import_name | service_relative | dynamic

_FROM_IMPORT_NAMES_RE = re.compile(
    r"^\s*from\s+([a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*)\s+import\s+([^\n\\]+)",
    re.MULTILINE,
)
_DYNAMIC_RE = re.compile(
    r"(?:importlib(?:\.\w+)*\.(?:import_module|load_module|__import__)"
    r"|__import__\s*\(|imp\.load_)"
)
_NAME_TOKEN_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


def _from_import_name_misses(text: str, root: Path, *, abs_re, lib_file_for) -> int:
    """Count ``from X import name`` where ``X.name`` resolves under libs but was not enqueued."""
    enqueued = set(abs_re.findall(text))
    missed = 0
    for match in _FROM_IMPORT_NAMES_RE.finditer(text):
        head, names_blob = match.group(1), match.group(2).split("#")[0]
        for part in names_blob.split(","):
            name = part.strip().split(" as ")[0].strip()
            if not name or name.startswith("(") or name == "*":
                continue
            if not _NAME_TOKEN_RE.match(name):
                continue
            cand = f"{head}.{name}"
            if cand not in enqueued and lib_file_for(root, cand) is not None:
                missed += 1
    return missed


@lru_cache(maxsize=64)
def measure_import_grammar_blinds(
    slug: str,
    root_str: str = "",
) -> frozenset[BlindClass]:
    """Return applicable blind class names for *slug*'s service tree.

    Empty frozenset means all three measured zero — a ``contradicted`` omit for
    that slug is earned under Fork 1. Non-empty means measured incompleteness.
    """
    # Lazy import avoids cycle with consumer_import_verify (which calls us).
    from implement_admission.consumer_import_verify import (
        _ABS_IMPORT_RE,
        _REL_FROM_RE,
        _lib_file_for_module,
        _service_dir,
        _tree_files,
        repo_root,
    )

    base_root = Path(root_str) if root_str else repo_root()
    service_dir = _service_dir(str(base_root), slug)
    if service_dir is None:
        return frozenset()
    base = Path(service_dir)
    if not base.is_dir():
        return frozenset()

    service_relative = 0
    from_import_name = 0
    dynamic = 0
    for path in _tree_files(base):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        service_relative += len(list(_REL_FROM_RE.finditer(text)))
        from_import_name += _from_import_name_misses(
            text, base_root, abs_re=_ABS_IMPORT_RE, lib_file_for=_lib_file_for_module
        )
        dynamic += len(list(_DYNAMIC_RE.finditer(text)))

    found: set[str] = set()
    if service_relative:
        found.add("service_relative")
    if from_import_name:
        found.add("from_import_name")
    if dynamic:
        found.add("dynamic")
    return frozenset(found)


def clear_blind_caches() -> None:
    """Drop blind-measurement caches (tests that mutate trees)."""
    measure_import_grammar_blinds.cache_clear()


def format_blind_tag(blinds: frozenset[str] | set[str]) -> str | None:
    """Return ``import_grammar_blind:a|b`` fragment, or ``None`` when empty."""
    if not blinds:
        return None
    ordered = "|".join(sorted(blinds))
    return f"import_grammar_blind:{ordered}"


__all__ = [
    "BlindClass",
    "clear_blind_caches",
    "format_blind_tag",
    "measure_import_grammar_blinds",
]
