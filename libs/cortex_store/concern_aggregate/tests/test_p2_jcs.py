"""RFC 8785 JCS golden vectors (B9)."""

from __future__ import annotations

import pytest
from cortex_store.concern_aggregate.canonical_json import (
    CanonicalJsonError,
    jcs_serialize,
    material_fingerprint,
    parse_json_strict,
)

pytestmark = pytest.mark.offline


def test_appendix_b_sample() -> None:
    value = {
        "numbers": [333333333.33333329, 1e30, 4.50, 2e-3, 0e0],
        "string": "Hello World",
    }
    assert (
        jcs_serialize(value)
        == '{"numbers":[333333333.3333333,1e+30,4.5,0.002,0],"string":"Hello World"}'
    )


def test_utf16_key_sort_bmp_vs_astral() -> None:
    value = {"\U0001d455": 1, "f": 2}
    assert jcs_serialize(value) == '{"f":2,"\U0001d455":1}'


def test_rejects_nan_and_infinity() -> None:
    with pytest.raises(CanonicalJsonError):
        jcs_serialize(float("nan"))
    with pytest.raises(CanonicalJsonError):
        parse_json_strict('{"x": NaN}')


def test_rejects_unsafe_integer() -> None:
    with pytest.raises(CanonicalJsonError):
        jcs_serialize(2**53)


def test_fingerprint_prefix() -> None:
    fp = material_fingerprint({"a": 1})
    assert fp.startswith("sha256:")
