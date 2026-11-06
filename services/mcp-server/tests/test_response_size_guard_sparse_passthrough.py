"""Sparse document payload passthrough for response size guard."""

from __future__ import annotations

import pytest
from response_size_guard import _node_is_sparse_document_payload
from tools._sparse_document_read import SPARSE_POLICY_SLICE1

pytestmark = pytest.mark.offline


def test_sparse_policy_root_only_passthrough() -> None:
    assert _node_is_sparse_document_payload(
        {"sparse_policy": SPARSE_POLICY_SLICE1, "selection": "manifest"}
    )
    assert _node_is_sparse_document_payload(
        {"sparse_policy": SPARSE_POLICY_SLICE1, "selection": "export"}
    )
    assert not _node_is_sparse_document_payload({"selection": "manifest"})
    assert not _node_is_sparse_document_payload(
        {"sparse_policy": "other", "selection": "manifest"}
    )


def test_multi_selection_passes_on_root_marker_without_batch_flag() -> None:
    assert _node_is_sparse_document_payload(
        {"sparse_policy": SPARSE_POLICY_SLICE1, "selection": "multi"}
    )
    assert not _node_is_sparse_document_payload({"sparse_section_batch": True})
    assert not _node_is_sparse_document_payload(
        {"sparse_explicit_export": True, "selection": "export"}
    )


def test_nested_sparse_dict_does_not_passthrough() -> None:
    from response_size_guard import _sparse_document_read_passthrough

    class _Result:
        structured_content = {
            "wrapper": {
                "sparse_policy": SPARSE_POLICY_SLICE1,
                "selection": "manifest",
            }
        }

    assert _sparse_document_read_passthrough(_Result()) is False  # type: ignore[arg-type]
