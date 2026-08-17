"""Narrow shared admission primitives — tree probe and prompt safety only.

Charter: stateless signals consumed by implement_admission bridge.
No routing, no decision gates, no imports from implement_admission.
"""

from admission_common.prompt_safety import forbidden_token_reason
from admission_common.qualified_scalar import (
    PUBLICATION_BUILDER_CENSUS,
    AbsenceSemantics,
    AuthorityClass,
    QualifiedScalar,
    SurfaceDecl,
    UnqualifiedScalarError,
    seal,
)
from admission_common.tree_probe import probe_working_tree

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = ('git_integration_worker', 'stargate')

__all__ = (
    "AbsenceSemantics",
    "AuthorityClass",
    "PUBLICATION_BUILDER_CENSUS",
    "QualifiedScalar",
    "SurfaceDecl",
    "UnqualifiedScalarError",
    "forbidden_token_reason",
    "probe_working_tree",
    "seal",
)
