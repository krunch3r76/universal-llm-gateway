"""Admission proxy ops — thin re-export surface for implement_admission gate stack."""

from __future__ import annotations

from ._doc_template import _op_doc_template
from ._doc_validate import _op_doc_validate
from ._implement_ready_preflight import _op_implement_ready_preflight

__all__ = [
    "_op_implement_ready_preflight",
    "_op_doc_template",
    "_op_doc_validate",
]
