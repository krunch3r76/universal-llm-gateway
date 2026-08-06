"""Shared claim-register vocabulary — observed vs derived for fleet claims.

Importable by ``cursor_auto``, ``implement_admission``, and
``charter_runner_store``. Housing this only inside cursor_auto would recreate
the soft-fix failure at package boundaries (row 29 conferral).

Public surface: ``ClaimRegister``, ``Claimed``, ``render_claim``, wire normalize
for the ``post_terminal_status`` partial guard. See ``wire`` module docstring
for the named absence (members 2, 5, 6 not covered).
"""

from claim_register.render import render_claim
from claim_register.types import (
    CLAIM_REGISTER_UNKNOWN,
    Claimed,
    ClaimRegister,
    claimed_derived,
    claimed_observed,
)
from claim_register.wire import (
    CLAIM_BEARING_KEYS,
    normalize_claim_bearing_payload,
)

__all__ = (
    "CLAIM_BEARING_KEYS",
    "CLAIM_REGISTER_UNKNOWN",
    "ClaimRegister",
    "Claimed",
    "claimed_derived",
    "claimed_observed",
    "normalize_claim_bearing_payload",
    "render_claim",
)
