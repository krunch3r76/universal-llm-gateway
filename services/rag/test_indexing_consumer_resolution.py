"""Regression: indexing._delete_file must be importable via the indexing module.

Exemplar (c) for the cursorbuild green-gate-verifies-consumers gate (commit 86f3afa3
modularize split indexing into sub-modules; the prior head left watcher_runtime
calling indexing._delete_file while the impl lived only in _indexing_delete.py with
no re-export).  An import-time resolution check catches this class of regression at
test time before a live watcher tries to delete a file.
"""

from __future__ import annotations

import inspect

from services.rag.rag_service import indexing


def test_indexing_module_exports_delete_file() -> None:
    """indexing._delete_file must be an attribute of the indexing module.

    Catches: modularization splits that move _delete_file to _indexing_delete.py
    without re-exporting it from indexing.__init__ or indexing.py, leaving
    watcher_runtime's ``indexing._delete_file(path)`` call to raise AttributeError
    on every file deletion.
    """
    assert hasattr(indexing, "_delete_file"), (
        "indexing._delete_file not found — module split likely dropped the re-export; "
        "add ``from ._indexing_delete import _delete_file as _delete_file`` to indexing.py"
    )


def test_indexing_delete_file_is_async_callable() -> None:
    """_delete_file must be an async function accepting a Path argument."""
    fn = indexing._delete_file
    assert callable(fn), "indexing._delete_file is not callable"
    assert inspect.iscoroutinefunction(fn), (
        "indexing._delete_file must be async (watcher_runtime calls it with await)"
    )
    sig = inspect.signature(fn)
    params = list(sig.parameters)
    assert params, "indexing._delete_file must accept at least one argument (file_path)"
