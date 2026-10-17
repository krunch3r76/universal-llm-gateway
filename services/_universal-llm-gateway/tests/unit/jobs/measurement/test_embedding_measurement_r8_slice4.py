"""Test 4.33 — embedding measurement reads per-profile ubatch_size."""

from __future__ import annotations

from src.jobs.measurement.embedding_profile_loader import merged_profile_loader


def test_4_33_profile_ubatch_merged() -> None:
    entry = {
        "loader": {"embedding": True, "ubatch_size": 8192},
        "devices": {
            "gpu": {
                "profiles": {
                    "8192": {"loader": {"ubatch_size": 2048, "pooling": "last"}},
                }
            }
        },
    }
    merged = merged_profile_loader(entry, ctx=8192, mode="gpu")
    assert merged["ubatch_size"] == 2048
    assert merged["pooling"] == "last"
    assert merged["embedding"] is True
