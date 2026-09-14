"""split_verbatim_layer must not raise when verbatim_bytes cuts a codepoint.

Specimen: agent-bus:10479 2026-09-13 — POST /threads/10479/resume-fence 500
UnicodeDecodeError at byte 3864-3865 inside a 3-byte character (′ U+2032).
"""

from __future__ import annotations

import pytest

from cortex_store.verbatim_succession import split_verbatim_layer

pytestmark = pytest.mark.offline


def test_split_verbatim_layer_mid_prime_does_not_raise() -> None:
    text = "BIND A′+D-fold"
    raw = text.encode("utf-8")
    prime_at = raw.index("′".encode())
    cut = prime_at + 1
    assert raw[cut] & 0xC0 == 0x80
    out = split_verbatim_layer(text, verbatim_bytes=cut)
    assert "BIND A" in out
    assert "′" not in out


def test_split_verbatim_layer_exact_boundary_keeps_prime() -> None:
    text = "BIND A′+D-fold"
    raw = text.encode("utf-8")
    end = raw.index("′".encode()) + len("′".encode())
    out = split_verbatim_layer(text, verbatim_bytes=end)
    assert out.startswith("BIND A′")
