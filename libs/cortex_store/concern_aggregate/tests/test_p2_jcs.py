"""RFC 8785 JCS golden vectors (B9)."""

from __future__ import annotations

import pytest
from cortex_store.concern_aggregate.canonical_json import (
    _MATERIAL_MAX_BYTES,
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


def test_utf16_key_sort_astral_vs_private_use() -> None:
    """UTF-16 code-unit order: astral before \\ue000 (not code-point order)."""
    value = {"\U0001d455": 1, "\ue000": 2}
    assert jcs_serialize(value) == '{"\U0001d455":1,"\ue000":2}'


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1e-7, "1e-7"),
        (1e-6, "0.000001"),
        (1e-5, "0.00001"),
        (9.999999999999997e-7, "9.999999999999997e-7"),
        (1e16, "10000000000000000"),
        (float(2**53), "9007199254740992"),
        (1.8014398509481984e16, "18014398509481984"),
        (9.999999999999997e20, "999999999999999700000"),
    ],
)
def test_es6_number_vectors(value: float, expected: str) -> None:
    assert jcs_serialize(value) == expected


def test_rejects_nan_and_infinity() -> None:
    with pytest.raises(CanonicalJsonError):
        jcs_serialize(float("nan"))
    with pytest.raises(CanonicalJsonError):
        parse_json_strict('{"x": NaN}')


def test_rejects_unsafe_integer() -> None:
    with pytest.raises(CanonicalJsonError):
        jcs_serialize(2**53)


def test_rejects_lone_surrogate_in_string() -> None:
    with pytest.raises(CanonicalJsonError):
        jcs_serialize("\ud800")


def test_rejects_non_string_object_key() -> None:
    with pytest.raises(CanonicalJsonError):
        jcs_serialize({1: "x"})


def test_rejects_material_size_cap() -> None:
    big = {"x": "a" * (_MATERIAL_MAX_BYTES + 1)}
    with pytest.raises(CanonicalJsonError):
        material_fingerprint(big)


def test_fingerprint_prefix() -> None:
    fp = material_fingerprint({"a": 1})
    assert fp.startswith("sha256:")
