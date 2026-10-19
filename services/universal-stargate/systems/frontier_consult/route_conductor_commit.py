"""Post-GIW conductor birth writes — packet file, scoreboard tip, journal."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from implement_admission.admission_read import compute_packet_sha256, replace_frontmatter_value
from implement_admission.conductor_score_journal import birth_scoreboard, read_tip
from implement_admission.source_ref import todo_slug_from_ref

from .handoff import _workspaces_root


@dataclass(frozen=True, slots=True)
class ConductorCommitPlan:
    """Pure admit plan fields required to commit after GIW 2xx."""

    source_ref: str
    packet_markdown: str
    packet_sha256: str
    packet_rel_path: str
    birth_scoreboard_markdown: str | None
    hop_seq: int
    birth_seed_ignored: bool = False


@dataclass(frozen=True, slots=True)
class ConductorCommitResult:
    packet_path: str
    packet_sha256: str
    birth_seed_ignored: bool


def _materialized_dir(workspaces_root: Path) -> Path:
    root = workspaces_root.resolve()
    repo = root
    if root.name != "universal-llm-gateway":
        nested = root / "universal-llm-gateway"
        if nested.is_dir():
            repo = nested
    return repo / "tmp/implement-admission/materialized"


def commit_conductor_plan(
    plan: ConductorCommitPlan,
    dispatch_id: str,
    *,
    workspaces_root: Path | None = None,
) -> ConductorCommitResult:
    """Write conductor packet and birth scoreboard only after GIW admits.

    Idempotent when the on-disk packet already matches ``plan.packet_sha256``.
    Skips scoreboard birth when a tip already exists (continuation hop).
    """
    if not str(dispatch_id or "").strip():
        msg = "commit_conductor_plan requires dispatch_id from GIW 2xx"
        raise ValueError(msg)
    root = (workspaces_root or _workspaces_root()).resolve()
    out_dir = _materialized_dir(root)
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = todo_slug_from_ref(plan.source_ref)
    out_path = out_dir / f"conductor-{slug}.md"
    rel = plan.packet_rel_path
    if not rel:
        rel = f"universal-llm-gateway/tmp/implement-admission/materialized/conductor-{slug}.md"

    existing_tip = read_tip(slug, files_root=root)
    if plan.hop_seq >= 1 and existing_tip is None:
        from .admission import FrontierEndpointError

        raise FrontierEndpointError(
            request_id="",
            field="scoreboard",
            reason="scoreboard tip required for conductor continuation hop",
            status_code=422,
            code="conductor_tip_required",
        )

    birth_seed_ignored = plan.birth_seed_ignored or existing_tip is not None
    digest = plan.packet_sha256
    if out_path.is_file():
        on_disk = out_path.read_text(encoding="utf-8")
        on_sha = compute_packet_sha256(on_disk)
        if on_sha != digest:
            text = replace_frontmatter_value(plan.packet_markdown, "packet_sha256", digest)
            out_path.write_text(text, encoding="utf-8")
    else:
        text = replace_frontmatter_value(plan.packet_markdown, "packet_sha256", digest)
        out_path.write_text(text, encoding="utf-8")

    if (
        not birth_seed_ignored
        and plan.birth_scoreboard_markdown
        and existing_tip is None
        and plan.hop_seq == 0
    ):
        birth_scoreboard(
            slug,
            scoreboard_body=plan.birth_scoreboard_markdown,
            seat="materializer",
            dispatch_id=dispatch_id,
            reason="conductor spawn birth",
            rows=(),
            delta="sparse birth",
            files_root=root,
        )

    return ConductorCommitResult(
        packet_path=rel,
        packet_sha256=digest,
        birth_seed_ignored=birth_seed_ignored,
    )


def plan_from_admit_dict(plan: dict[str, Any]) -> ConductorCommitPlan | None:
    """Build commit plan when admit pipeline returned conductor render fields."""
    markdown = plan.get("packet_markdown")
    if not isinstance(markdown, str) or not markdown.strip():
        return None
    sha = str(plan.get("packet_sha256") or "").strip()
    if not sha:
        sha = hashlib.sha256(markdown.encode()).hexdigest()
    source_ref = str(plan.get("source_ref") or "").strip()
    if not source_ref:
        return None
    hop_seq_raw = plan.get("hop_seq")
    hop_seq = int(hop_seq_raw) if isinstance(hop_seq_raw, int) else 0
    rel = str(plan.get("packet_rel_path") or "").strip()
    birth_md = plan.get("birth_scoreboard_markdown")
    birth_body = birth_md if isinstance(birth_md, str) else None
    ignored = bool(plan.get("birth_seed_ignored"))
    return ConductorCommitPlan(
        source_ref=source_ref,
        packet_markdown=markdown,
        packet_sha256=sha,
        packet_rel_path=rel,
        birth_scoreboard_markdown=birth_body,
        hop_seq=hop_seq,
        birth_seed_ignored=ignored,
    )
