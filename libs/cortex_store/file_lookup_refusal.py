"""Refuse entity lookup when the ref is an existing cortex file.

Cursor seats pass ``cortex://notes/...`` to ``entity_get`` / ``resolve``.
Those ops parse the URI as an entity id (``notes:system/...``) and return
404, so the seat drops a file that exists. File paths belong to
``fs(op="read")``.

A top-level directory whose name matches an entity type (``agent-bus/``)
must not refuse: ``cortex://agent-bus/7182`` is an entity. Refuse only when
the rebuilt path is a file inside the files root.
"""

from __future__ import annotations

from pathlib import Path

from implement_admission.closeout_helpers import cortex_files_root

# 422 — wrong tool, not a missing entity. Literal avoids the deprecated
# Starlette alias HTTP_422_UNPROCESSABLE_ENTITY.
FILE_LOOKUP_STATUS = 422


def file_lookup_refusal(ref: str, *, cortex_root: Path | None = None) -> str | None:
    """Return an fs-read instruction when *ref* is an existing cortex file.

    ``cortex://``, ``cortex:``, and ``type:a/b`` all use the same rule: the
    rebuilt path must be a file inside the files root. A missing file, a
    directory, a traversal, or an entity URI falls through to entity lookup.
    """
    raw = ref.strip()
    rel = _file_rel(raw)
    if rel is None:
        return None
    root = (cortex_root if cortex_root is not None else cortex_files_root()).resolve()
    if _existing_cortex_file(rel, root) is None:
        return None
    uri = f"cortex://{rel.strip('/')}"
    return (
        f"{raw!r} is a cortex file, not an entity id. "
        f'Read it with fs(op="read", path="{uri}").'
    )


def _file_rel(raw: str) -> str | None:
    lower = raw.lower()
    if lower.startswith("cortex://"):
        rel = raw[len("cortex://") :].lstrip("/")
        return rel or None
    if lower.startswith("cortex:"):
        rel = raw[len("cortex:") :].lstrip("/")
        return rel or None
    if "://" in raw or ":" not in raw:
        return None
    type_name, rest = raw.split(":", 1)
    if not type_name or "/" not in rest:
        return None
    return f"{type_name}/{rest.lstrip('/')}"


def _existing_cortex_file(rel: str, root: Path) -> Path | None:
    rel = rel.strip("/")
    first = rel.split("/", 1)[0]
    if not rel or not first or ":" in first:
        return None
    root = root.resolve()
    candidate = (root / rel).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate
