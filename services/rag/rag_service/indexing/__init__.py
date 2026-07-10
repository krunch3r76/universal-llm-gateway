"""Indexing package — file-level index/delete funnel and phase helpers.

Public surface preserved for ``from services.rag.rag_service import indexing``
and ``indexing._index_file`` / ``indexing._delete_file`` call sites.
"""

from __future__ import annotations

from .delete import _delete_file as _delete_file
from .index_file import _index_file as _index_file
from .source_identity import _should_skip_cached_source as _should_skip_cached_source

__all__ = [
    "_delete_file",
    "_index_file",
    "_should_skip_cached_source",
]
