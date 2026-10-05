"""Pin ``conductor dispatch_id`` and L1 on the G5 nest hop (a:37920)."""

from __future__ import annotations

import re
from pathlib import Path

from universal_protocol.errors import ProtocolError

from implement_admission.closeout_helpers import cortex_files_root
from implement_admission.conductor_score_journal import (
    G_ROWS,
    forward_mutate_tip,
    read_tip,
)
from implement_admission.conductor_witness_table import (
    _conductor_dispatch_id,
    _repo_head_full_sha,
)

# Same set as GIW nested-implement witness jobs. Kept here so pin does not
# import the worker package.
_IMPLEMENT_JOBS = frozenset(
    {"implement", "pure-mechanical", "mechanical", "none", "freeform"}
)
_L1_PENDING_RE = re.compile(
    r"^(\|\s*L1\s*\|\s*)\(pending\)(\s*\|)",
    re.MULTILINE | re.IGNORECASE,
)


def _todo_slug(work_key: str | None) -> str | None:
    raw = (work_key or "").strip()
    if not raw.startswith("todo:"):
        return None
    slug = raw.split(":", 1)[1].strip()
    return slug or None


def _with_dispatch_id_line(tip_body: str, parent_dispatch_id: str) -> str:
    if _conductor_dispatch_id(tip_body):
        return tip_body
    line = f"conductor dispatch_id `{parent_dispatch_id}`"
    body = tip_body.rstrip()
    return f"{body}\n{line}\n"


def _with_l1_lane_head(tip_body: str, repo: Path | None) -> str:
    if repo is None:
        return tip_body
    head = _repo_head_full_sha(repo)
    if not head:
        return tip_body
    if _L1_PENDING_RE.search(tip_body):
        return _L1_PENDING_RE.sub(rf"\g<1>{head} on master\g<2>", tip_body, count=1)
    return tip_body


def pin_g5_nest_dispatch_id(
    *,
    parent_dispatch_id: str,
    parent_contract: str | None,
    child_contract: str | None,
    work_key: str | None,
    source_repo: str | None,
    files_root: Path | None = None,
) -> str | None:
    """Write the G5 nest pin onto the scoreboard tip when one already exists.

    Skips when this admit is not a conductor→implement nest, or when no tip
    exists yet. If a tip exists and the pin line is still missing after the
    write, Composer must not proceed.
    """
    if str(parent_contract or "").strip().lower() != "conductor":
        return None
    if str(child_contract or "").strip().lower() not in _IMPLEMENT_JOBS:
        return None
    slug = _todo_slug(work_key)
    if slug is None:
        return None
    root = files_root if files_root is not None else cortex_files_root()
    prior = read_tip(slug, files_root=root)
    if prior is None:
        return None
    repo = Path(source_repo) if source_repo else None
    next_body = _with_l1_lane_head(
        _with_dispatch_id_line(prior[0], parent_dispatch_id),
        repo,
    )
    if next_body != prior[0]:
        result = forward_mutate_tip(
            slug,
            next_body=next_body,
            seat="giw",
            dispatch_id=parent_dispatch_id,
            reason="g5 nest pin conductor dispatch_id + L1",
            rows=G_ROWS,
            delta=f"conductor dispatch_id `{parent_dispatch_id}`",
            files_root=root,
        )
        if result.rejected_reason:
            raise ProtocolError(
                code="CURSOR_G5_NEST_DISPATCH_ID_UNPINNED",
                message=(
                    "G5 nest pin rejected: "
                    f"{result.rejected_reason}; tip must contain "
                    f"conductor dispatch_id `{parent_dispatch_id}` before Composer admit"
                ),
                source="worker",
                retryable=False,
                data={"work_key": work_key, "parent_dispatch_id": parent_dispatch_id},
            )
    stamped = read_tip(slug, files_root=root)
    body = stamped[0] if stamped is not None else next_body
    pinned = _conductor_dispatch_id(body)
    if not pinned:
        raise ProtocolError(
            code="CURSOR_G5_NEST_DISPATCH_ID_UNPINNED",
            message=(
                "G5 nest requires tip line "
                f"conductor dispatch_id `{parent_dispatch_id}` before Composer admit"
            ),
            source="worker",
            retryable=False,
            data={"work_key": work_key, "parent_dispatch_id": parent_dispatch_id},
        )
    return pinned
