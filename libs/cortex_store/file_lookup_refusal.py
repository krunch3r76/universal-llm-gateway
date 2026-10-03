"""Refuse entity lookup when the ref is a cortex file path.

Cursor seats pass ``cortex://notes/...`` to ``entity_get`` / ``resolve``.
Those ops parse the URI as an entity id (``notes:system/...``) and return
404, so the seat drops a file that exists. File paths belong to
``fs(op="read")``.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from implement_admission.closeout_helpers import cortex_files_root
from implement_admission.share_uri_registry import is_cortex_entity_uri

# 422 — wrong tool, not a missing entity. Literal avoids the deprecated
# Starlette alias HTTP_422_UNPROCESSABLE_ENTITY.
FILE_LOOKUP_STATUS = 422


def file_lookup_refusal(ref: str, *, cortex_root: Path | None = None) -> str | None:
    """Return an fs-read instruction when *ref* is a cortex file, else None.

    ``cortex://`` / ``cortex:`` forms use the existence-first file discriminator
    (leading segment exists under the files root). The ``type:a/b`` form — what
    ``resolve`` stores after mis-parsing ``cortex://type/a/b`` — refuses only
    when that reconstructed path is an existing file, so real entity ids that
    happen to contain a slash stay on the entity path.
    """
    raw = ref.strip()
    classified = _classify(raw)
    if classified is None:
        return None
    rel, require_file = classified
    root = (cortex_root if cortex_root is not None else cortex_files_root()).resolve()
    if is_cortex_entity_uri(rel, cortex_root=root):
        return None
    if require_file and not _is_existing_file(root, rel):
        return None
    uri = f"cortex://{rel.lstrip('/')}"
    return (
        f"{raw!r} is a cortex file, not an entity id. "
        f'Read it with fs(op="read", path="{uri}").'
    )


def _classify(raw: str) -> tuple[str, bool] | None:
    lower = raw.lower()
    if lower.startswith("cortex://"):
        rel = raw[len("cortex://") :].lstrip("/")
        return (rel, False) if rel else None
    if lower.startswith("cortex:"):
        rel = raw[len("cortex:") :].lstrip("/")
        return (rel, False) if rel else None
    if "://" in raw or ":" not in raw:
        return None
    type_name, rest = raw.split(":", 1)
    if not type_name or "/" not in rest:
        return None
    if ".." in PurePosixPath(rest).parts:
        return None
    return f"{type_name}/{rest.lstrip('/')}", True


def _is_existing_file(root: Path, rel: str) -> bool:
    if ".." in PurePosixPath(rel).parts:
        return False
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return candidate.is_file()
