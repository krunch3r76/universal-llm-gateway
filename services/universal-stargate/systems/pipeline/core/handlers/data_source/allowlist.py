"""SQLite ``db_path`` allowlist for the ``data_source_v1`` ``sqlite_query`` source.

Defines the fixed set of databases the handler may open (RAG metadata, Cortex,
and Cortex todos stores under the user's home directory) and resolves a
caller-supplied path string against that allowlist. Any path that resolves
outside the allowed set is rejected so a pipeline step cannot read arbitrary
files off disk through the SQLite reader.
"""

from __future__ import annotations

from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)

_DEFAULT_METADATA_DB = Path.home() / ".rag" / "store" / "rag_metadata.db"
_DEFAULT_CORTEX_DB = Path.home() / ".cortex" / "cortex.db"
_DEFAULT_TODOS_DB = Path.home() / ".cortex" / "todos.db"


def expand_allowed_db(path_str: str) -> Path | None:
    """Expand and resolve a path string, returning it only if it's in the allowlist.

    Args:
        path_str: The path string to expand and resolve.

    Returns:
        A `Path` object if the resolved path is in the allowed list, otherwise `None`.
    """
    raw = path_str.strip()
    if raw.startswith("~/"):
        p = Path.home() / raw[2:]
    else:
        p = Path(raw).expanduser()
    try:
        resolved = p.resolve()
    except OSError:
        return None
    allowed = {
        _DEFAULT_METADATA_DB.resolve(),
        _DEFAULT_CORTEX_DB.resolve(),
        _DEFAULT_TODOS_DB.resolve(),
    }
    if resolved not in allowed:
        logger.error("data_source_v1: rejected db path outside allowlist: %s", resolved)
        return None
    return resolved
