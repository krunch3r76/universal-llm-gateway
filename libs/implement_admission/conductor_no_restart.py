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
    "G7 land closeout must copy the lines of "
    "tmp/reviews/land-receipts/<after_sha>.md in the hub checkout, or the "
    "`restart_owed_block` from git_land, verbatim. A block that reads "
    "unavailable is copied as it stands and is not recomputed. If neither "
    "exists, call implement_admission.restart_owed.restart_owed_for_range"
    "(repo, before_sha, after_sha) using master_before_sha and "
    "master_after_sha from that same git_land envelope when both fields are "
    "present; otherwise use the before and after SHAs of the same hub master "
    "move (the Updating line of that merge, or rev-parse of refs/heads/master "
    "immediately before and after that command). If you do not have both SHAs "
    "from that same move, write `restart_owed: unavailable (missing_shas)` "
    "and stop. If the helper raises, write "
    "`restart_owed: unavailable (<exception class name>)` and stop. "
    "Do not assemble a path list. Do not call manage. The operator seat "
    "restarts the named slugs."
)
