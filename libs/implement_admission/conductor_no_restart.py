"""No-restart sentence pasted into conductor packets and nested prompts.

Friction a:37762: a nested seat cannot see cross-lane busy state. The operator
seat restarts after G7 land.
"""

from __future__ import annotations

NO_RESTART_LINE = (
    "No manage restart, stop, start, rebuild, or force, and no propagate: "
    "a restart from this seat kills work it cannot see on other lanes; "
    "the operator seat restarts after G7 land using restart_owed:."
)

NESTED_PROMPT_RULE = (
    "Every nested implement and land prompt you author must include this "
    f"line verbatim: {NO_RESTART_LINE}"
)

G7_RESTART_OWED_RULE = (
    "G7 land closeout must include the `restart_owed:` line from "
    "implement_admission.restart_owed.restart_owed_line applied to the "
    "landed diff (slug_for_service_path and _SERVICE_OWNERSHIP path_prefix; "
    "a path no rule maps is `unmapped:`, not dropped). "
    "Do not call manage. The operator seat restarts the named slugs."
)
