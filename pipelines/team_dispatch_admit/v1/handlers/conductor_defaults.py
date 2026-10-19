"""Conductor job defaults for team-dispatch-admit (Shape A)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from implement_admission.conductor_materialize import (
    G_ROWS,
    load_conductor_context,
    rematerialize_context_from_dispatch,
    render_conductor_packet,
)
from implement_admission.conductor_policy_snapshot import (
    default_knobs_from_route_policy,
    model_for_seat_role,
    plan_scoreboard_birth,
    resolve_conductor_knobs,
    selected_seat_for_hop,
)
from implement_admission.conductor_score_journal import (
    read_tip,
    render_sparse_scoreboard,
)
from implement_admission.source_ref import todo_slug_from_ref

from ._envelope import admit_envelope, pass_through_reject, reject_envelope


def _pipeline_conductor(opts: dict[str, Any]) -> dict[str, Any]:
    raw = opts.get("pipeline_options") or opts.get("generation_options") or {}
    if isinstance(raw, dict) and "conductor" in raw:
        block = raw.get("conductor")
        return block if isinstance(block, dict) else {}
    cond = opts.get("conductor")
    return cond if isinstance(cond, dict) else {}


def _hop_seq(opts: dict[str, Any]) -> int:
    raw = opts.get("hop_seq")
    if isinstance(raw, int):
        return raw
    return 0


def evaluate_conductor_defaults(
    opts: dict[str, Any], validate_out: dict[str, Any]
) -> dict[str, Any]:
    prior = pass_through_reject(validate_out)
    if prior.get("admitted") is False:
        return prior

    conductor_opts = _pipeline_conductor(opts)
    hop_seq = _hop_seq(opts)
    rebind = bool(conductor_opts.get("rebind_policy"))
    stamped = default_knobs_from_route_policy()
    snapshot = resolve_conductor_knobs(
        conductor_opts, prior=stamped, rebind_policy=rebind
    )
    seat_role = selected_seat_for_hop(
        hop_seq=hop_seq,
        next_admit_verb=opts.get("next_admit_verb"),
        hop_reason=opts.get("hop_reason"),
    )
    model = model_for_seat_role(snapshot, seat_role)
    source_ref = str(opts.get("source_ref") or "").strip()
    files_root = Path(opts.get("files_root") or "/tmp")
    slug = todo_slug_from_ref(source_ref) if source_ref.startswith("todo:") else ""
    tip_entry_gate: str | None = None
    tip_present = False
    if slug:
        tip = read_tip(slug, files_root=files_root)
        tip_present = tip is not None
        if tip is not None:
            from implement_admission.conductor_score_table import (
                row_id_in,
            )

            body = tip[0]
            for line in body.splitlines():
                rid = row_id_in(line)
                if rid and rid == (conductor_opts.get("entry_gate") or ""):
                    tip_entry_gate = rid
            if "**Entry gate:**" in body:
                for line in body.splitlines():
                    if line.strip().startswith("- **Entry gate:**"):
                        tip_entry_gate = line.split(":", 1)[-1].strip()
                        break

    birth_plan = plan_scoreboard_birth(
        conductor_opts,
        hop_seq=hop_seq,
        tip_present=tip_present,
        default_rows=G_ROWS,
        default_entry_gate="G1",
        tip_entry_gate=tip_entry_gate,
    )
    birth_md: str | None = None
    if hop_seq == 0 and birth_plan.rows and not tip_present:
        birth_md = render_sparse_scoreboard(
            source_ref=source_ref or "todo:unknown",
            slug=slug or "unknown",
            entry_gate=birth_plan.entry_gate or "G1",
            stop_after=birth_plan.stop_after,
            rows=birth_plan.rows,
        )

    packet_markdown = ""
    packet_sha256 = ""
    packet_rel_path = ""
    if source_ref and not opts.get("_skip_packet_render"):
        rematerialize = rematerialize_context_from_dispatch(
            hop_seq=hop_seq,
            hop_from=opts.get("hop_from"),
            dispatch_thread_id=opts.get("dispatch_thread_id"),
            generation_options=opts.get("generation_options") or {},
        )

        class _StubCortex:
            def entity_get(self, *_a: object, **_k: object) -> dict[str, Any]:
                attrs = opts.get("_test_todo_attrs") or {}
                return {
                    "id": 1,
                    "name": attrs.get("name") or "test",
                    "attributes": attrs,
                }

        ctx = load_conductor_context(
            source_ref,
            cortex=_StubCortex(),  # type: ignore[arg-type]
            rematerialize=rematerialize,
        )
        out_dir = files_root / "tmp/implement-admission/materialized"
        rendered = render_conductor_packet(
            ctx,
            out_dir=out_dir,
            repo_root=files_root,
            hop_seq=hop_seq,
            write_birth_scoreboard=birth_md is not None,
        )
        packet_markdown = rendered.text
        packet_sha256 = rendered.packet_sha256
        packet_rel_path = rendered.packet_rel_path
        if birth_md is None and rendered.birth_scoreboard_markdown:
            birth_md = rendered.birth_scoreboard_markdown

    plan = admit_envelope(
        backend="cursor_sdk",
        role=None,
        seat="cursor-sdk",
        model=model,
        job="conductor",
        workflow=None,
        to_agent="cursor-sdk",
        family="cursor",
        platform="sdk",
    )
    plan.update(
        {
            "packet_markdown": packet_markdown,
            "packet_sha256": packet_sha256,
            "packet_rel_path": packet_rel_path,
            "birth_scoreboard_markdown": birth_md,
            "selected_seat_role": seat_role,
            "birth_seed_ignored": birth_plan.birth_seed_ignored,
            "policy_sha256": snapshot.policy_sha256,
            "policy_epoch": snapshot.policy_epoch,
            "entry_gate": birth_plan.entry_gate,
            "hop_seq": hop_seq,
            "hop_from": opts.get("hop_from"),
            "hop_reason": opts.get("hop_reason"),
            "work_key": source_ref,
            "tip_mutated": opts.get("tip_mutated"),
            "child_dispatched": opts.get("child_dispatched"),
            "hop_class_defaulted": not (
                opts.get("next_admit_verb") or opts.get("hop_reason")
            )
            and hop_seq >= 1,
        }
    )
    return plan


def validate_conductor_policy_drift(opts: dict[str, Any]) -> dict[str, Any] | None:
    if str(opts.get("job") or "").strip().lower() != "conductor":
        return None
    conductor_opts = _pipeline_conductor(opts)
    rebind = bool(conductor_opts.get("rebind_policy"))
    stamped = default_knobs_from_route_policy()
    from implement_admission.conductor_policy_snapshot import conductor_policy_drift

    if conductor_policy_drift(conductor_opts, stamped=stamped, rebind_policy=rebind):
        return reject_envelope(
            status_code=422,
            code="conductor_policy_drift",
            field="conductor",
            reason="conductor knob delta without rebind_policy",
        )
    return None


def validate_conductor_tip_required(opts: dict[str, Any]) -> dict[str, Any] | None:
    if str(opts.get("job") or "").strip().lower() != "conductor":
        return None
    if _hop_seq(opts) < 1:
        return None
    source_ref = str(opts.get("source_ref") or "").strip()
    if not source_ref.startswith("todo:"):
        return None
    slug = todo_slug_from_ref(source_ref)
    files_root = Path(opts.get("files_root") or "/tmp")
    if read_tip(slug, files_root=files_root) is None:
        return reject_envelope(
            status_code=422,
            code="conductor_tip_required",
            field="scoreboard",
            reason="scoreboard tip required for conductor continuation hop",
        )
    return None
