"""Settle gate for stargate functional_settle rows.

Waits on Event Service membership. Timeout and a short id set are
``indeterminate``. Only an affected pipeline missing or failing ``op=run``
after the gateway-id sets match is ``fail_attributable``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Literal

SettleVerdict = Literal["pass", "fail_attributable", "indeterminate"]

MEMBERSHIP_SIGNAL = "federation.gateway.membership"
CATALOG_CHANGED = "federation.catalog.changed"
REACHABILITY_RESTORED = "federation.gateway.reachability.restored"
PROGRESS_SIGNALS = frozenset({CATALOG_CHANGED, REACHABILITY_RESTORED})
SETTLE_CAP_S = 120.0


def gateway_id_sets_equal(left: Iterable[str], right: Iterable[str]) -> bool:
    """Set equality on ``gateway_id``. Length equality is not membership."""
    return set(left) == set(right)


def affected_pipeline_ids(
    snapshot_pipeline_ids: Iterable[str],
    land_paths: Iterable[str],
    *,
    pipeline_sources: Mapping[str, Iterable[str]],
    step_type_modules: Mapping[str, str],
    pipeline_step_types: Mapping[str, Iterable[str]],
) -> set[str]:
    """Pipelines whose YAML or registering step-type module is in the land diff."""
    diff = set(land_paths)
    affected: set[str] = set()
    for pipeline_id in snapshot_pipeline_ids:
        sources = set(pipeline_sources.get(pipeline_id, ()))
        if sources & diff:
            affected.add(pipeline_id)
            continue
        for step_type in pipeline_step_types.get(pipeline_id, ()):
            module = step_type_modules.get(step_type)
            if module and module in diff:
                affected.add(pipeline_id)
                break
    return affected


def judge_settle(
    *,
    snapshot_gateway_ids: Iterable[str] | None,
    snapshot_pipeline_ids: Iterable[str] | None,
    post_gateway_ids: Iterable[str] | None,
    post_pipeline_ids: Iterable[str] | None,
    membership_seq: int | None,
    latest_catalog_seq: int | None,
    timed_out: bool,
    land_paths: Iterable[str] = (),
    pipeline_sources: Mapping[str, Iterable[str]] | None = None,
    step_type_modules: Mapping[str, str] | None = None,
    pipeline_step_types: Mapping[str, Iterable[str]] | None = None,
    op_run_failures: Iterable[str] = (),
) -> SettleVerdict:
    """Return the settle verdict. Cap expiry is never fail."""
    if timed_out:
        return "indeterminate"
    if snapshot_gateway_ids is None or post_gateway_ids is None:
        return "indeterminate"
    if not gateway_id_sets_equal(snapshot_gateway_ids, post_gateway_ids):
        return "indeterminate"
    if latest_catalog_seq is not None:
        if membership_seq is None or membership_seq <= latest_catalog_seq:
            return "indeterminate"
    snap_pipes = set(snapshot_pipeline_ids or ())
    post_pipes = set(post_pipeline_ids or ())
    affected = affected_pipeline_ids(
        snap_pipes,
        land_paths,
        pipeline_sources=pipeline_sources or {},
        step_type_modules=step_type_modules or {},
        pipeline_step_types=pipeline_step_types or {},
    )
    failures = set(op_run_failures)
    for pipeline_id in affected:
        if pipeline_id not in post_pipes or pipeline_id in failures:
            return "fail_attributable"
    missing_unrelated = (snap_pipes - post_pipes) - affected
    if missing_unrelated:
        return "indeterminate"
    return "pass"


def judge_event_window(
    events: Iterable[Mapping[str, Any]],
    *,
    snapshot_gateway_ids: Iterable[str] | None,
    snapshot_pipeline_ids: Iterable[str] | None,
    timed_out: bool,
    land_paths: Iterable[str] = (),
    pipeline_sources: Mapping[str, Iterable[str]] | None = None,
    step_type_modules: Mapping[str, str] | None = None,
    pipeline_step_types: Mapping[str, Iterable[str]] | None = None,
    op_run_failures: Iterable[str] = (),
) -> SettleVerdict:
    """Fold a post-restart event window into :func:`judge_settle`.

    ``gateway.state.changed`` is ignored. Progress events do not turn cap
    expiry into fail.
    """
    post_gateway_ids = None
    post_pipeline_ids = None
    membership_seq = None
    latest_catalog_seq = None
    for event in events:
        signal = str(event.get("signal") or "")
        payload = event.get("payload") or {}
        seq = event.get("seq")
        if signal == CATALOG_CHANGED and isinstance(seq, int):
            latest_catalog_seq = seq
        elif signal == MEMBERSHIP_SIGNAL:
            ids = payload.get("gateway_ids")
            if isinstance(ids, list):
                post_gateway_ids = [str(item) for item in ids]
            pipes = payload.get("pipeline_ids")
            if isinstance(pipes, list):
                post_pipeline_ids = [str(item) for item in pipes]
            if isinstance(seq, int):
                membership_seq = seq
    return judge_settle(
        snapshot_gateway_ids=snapshot_gateway_ids,
        snapshot_pipeline_ids=snapshot_pipeline_ids,
        post_gateway_ids=post_gateway_ids,
        post_pipeline_ids=post_pipeline_ids,
        membership_seq=membership_seq,
        latest_catalog_seq=latest_catalog_seq,
        timed_out=timed_out,
        land_paths=land_paths,
        pipeline_sources=pipeline_sources,
        step_type_modules=step_type_modules,
        pipeline_step_types=pipeline_step_types,
        op_run_failures=op_run_failures,
    )
