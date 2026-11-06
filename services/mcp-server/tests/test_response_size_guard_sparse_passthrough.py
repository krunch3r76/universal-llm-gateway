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


def test_multi_selection_requires_sparse_section_batch_marker() -> None:
    assert not _node_is_sparse_document_payload(
        {"sparse_policy": SPARSE_POLICY_SLICE1, "selection": "multi"}
    )
    assert _node_is_sparse_document_payload(
        {
            "sparse_policy": SPARSE_POLICY_SLICE1,
            "selection": "multi",
            "sparse_section_batch": True,
        }
    )
