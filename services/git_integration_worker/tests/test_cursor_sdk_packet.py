"""Lean-dispatch preamble harvest pointer (pytest matrix path)."""

from services.git_integration_worker.cursor_sdk_packet import _LEAN_DISPATCH_PREAMBLE


def test_lean_dispatch_preamble_harvest_pointer() -> None:
    assert "executive_envelope/effects_manifest" not in _LEAN_DISPATCH_PREAMBLE
    assert "sections=" not in _LEAN_DISPATCH_PREAMBLE
    assert "expected_read_sha256" not in _LEAN_DISPATCH_PREAMBLE
    assert "Selective reading of closeouts" in _LEAN_DISPATCH_PREAMBLE