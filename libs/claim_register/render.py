"""Render helpers — derived claims must never look like bare observations.

Without ``render_claim``, ``ClaimRegister`` is a dead field consumers ignore
(member-1 soft-fix class with more ceremony). Prose and summary authors must
go through this helper for claim-bearing text.
"""

from __future__ import annotations

from typing import Any

from claim_register.types import CLAIM_REGISTER_UNKNOWN, Claimed


def render_claim(claimed: Claimed[Any] | dict[str, Any]) -> str:
    """Render a claimed value with register-visible marking.

    - ``observed`` → bare assertive prose (value string) is allowed.
    - ``derived`` → MUST mark register; never bare observation-shaped prose.
    - ``unknown`` (wire degrade) → MUST mark loudly so untyped claims announce
      themselves.

    Raises:
        ValueError: missing register/value on a dict, or unrecognized register.
        TypeError: *claimed* is neither ``Claimed`` nor a wire dict.
    """
    register, value, basis = _unpack(claimed)
    text = value if isinstance(value, str) else repr(value)
    if register == "observed":
        return text
    if register == "derived":
        if basis:
            return f"(derived; basis={basis}) {text}"
        return f"derived: {text}"
    if register == CLAIM_REGISTER_UNKNOWN:
        if basis:
            return f"UNKNOWN_REGISTER ({basis}): {text}"
        return f"UNKNOWN_REGISTER: {text}"
    raise ValueError(f"unrecognized claim register {register!r}")


def _unpack(claimed: Claimed[Any] | dict[str, Any]) -> tuple[str, Any, str | None]:
    if isinstance(claimed, Claimed):
        return claimed.register, claimed.value, claimed.basis
    if isinstance(claimed, dict):
        if "register" not in claimed or "value" not in claimed:
            raise ValueError(
                "wire claim dict requires 'register' and 'value' keys; "
                f"got keys={sorted(claimed)!r}"
            )
        basis = claimed.get("basis")
        return str(claimed["register"]), claimed["value"], basis
    raise TypeError(
        f"render_claim expects Claimed or wire dict; got {type(claimed)!r}"
    )
