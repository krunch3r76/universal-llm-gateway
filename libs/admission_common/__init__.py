"""Narrow shared admission primitives — tree probe and prompt safety only.

Charter: stateless signals consumed by implement_admission bridge.
No routing, no decision gates, no imports from implement_admission.
"""

from admission_common.prompt_safety import forbidden_token_reason
from admission_common.tree_probe import probe_working_tree

__all__ = ("forbidden_token_reason", "probe_working_tree")
