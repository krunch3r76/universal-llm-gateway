"""Settle gate for stargate functional_settle rows.

Waits on Event Service membership. Timeout and a short id set are
``indeterminate``. Only an affected pipeline missing or failing ``op=run``
after the gateway-id sets match is ``fail_attributable``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

SettleVerdict = Literal["pass", "fail_attributable", "indeterminate"]
StopReason = Literal["ready", "cap", "stream_ended"]

MEMBERSHIP_SIGNAL = "federation.gateway.membership"
MEMBERSHIP_CATALOG_GATEWAYS_KEY = "catalog_gateway_ids"
CATALOG_CHANGED = "federation.catalog.changed"
REACHABILITY_RESTORED = "federation.gateway.reachability.restored"
PROGRESS_SIGNALS = frozenset({CATALOG_CHANGED, REACHABILITY_RESTORED})
SETTLE_CAP_S = 120.0


@dataclass(frozen=True)
class SettleWindow:
    """Latest membership in arrival order, plus catalogs seen strictly before it.

    ``catalog_gateway_ids is None`` means the latest membership was untagged
    or the key was not a list. ``membership_seq`` is diagnostic; live events
    carry none.
    """

    post_gateway_ids: tuple[str, ...] | None
    post_pipeline_ids: tuple[str, ...] | None
    catalog_gateway_ids: tuple[str, ...] | None
    catalog_arrivals: frozenset[str]
    membership_count: int
    membership_seq: int | None


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


def expected_absent_pipeline_ids(
    snapshot_pipeline_ids: Iterable[str],
    land_deleted_paths: Iterable[str],
    *,
    pipeline_sources: Mapping[str, Iterable[str]],
) -> set[str]:
    """Snapshot pipelines whose every listed source YAML is deleted at the land tip.

    An empty source list is not expected-absent. A partial delete (one source
    deleted, another only modified) stays out of this set and remains affected.
    """
    deleted = set(land_deleted_paths)
    expected: set[str] = set()
    for pipeline_id in snapshot_pipeline_ids:
        sources = set(pipeline_sources.get(pipeline_id, ()))
        if sources and sources <= deleted:
            expected.add(pipeline_id)
    return expected


def _payload(event: Mapping[str, Any]) -> Mapping[str, Any]:
    payload = event.get("payload")
    if isinstance(payload, Mapping):
        return payload
    return {}


def _str_tuple(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, list):
        return None
    return tuple(str(item) for item in value)


def _catalog_arrival_gateway(event: Mapping[str, Any]) -> str | None:
    """Gateway id of a positive ``catalog.changed``, or None."""
    payload = _payload(event)
    count = payload.get("new_model_count")
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        return None
    gateway_id = payload.get("gateway_id")
    if isinstance(gateway_id, str) and gateway_id:
        return gateway_id
    return None


def fold_settle_window(events: Iterable[Mapping[str, Any]]) -> SettleWindow:
    """Single pass in arrival order. Later catalogs do not back-fill the latest membership."""
    post_gateway_ids: tuple[str, ...] | None = None
    post_pipeline_ids: tuple[str, ...] | None = None
    catalog_gateway_ids: tuple[str, ...] | None = None
    catalog_arrivals: frozenset[str] = frozenset()
    membership_count = 0
    membership_seq: int | None = None
    arrivals_so_far: set[str] = set()
    for event in events:
        signal = str(event.get("signal") or "")
        if signal == CATALOG_CHANGED:
            gateway_id = _catalog_arrival_gateway(event)
            if gateway_id is not None:
                arrivals_so_far.add(gateway_id)
            continue
        if signal != MEMBERSHIP_SIGNAL:
            continue
        payload = _payload(event)
        post_gateway_ids = _str_tuple(payload.get("gateway_ids"))
        post_pipeline_ids = _str_tuple(payload.get("pipeline_ids"))
        catalog_gateway_ids = _str_tuple(payload.get(MEMBERSHIP_CATALOG_GATEWAYS_KEY))
        seq = event.get("seq")
        membership_seq = seq if isinstance(seq, int) and not isinstance(seq, bool) else None
        catalog_arrivals = frozenset(arrivals_so_far)
        membership_count += 1
    return SettleWindow(
        post_gateway_ids=post_gateway_ids,
        post_pipeline_ids=post_pipeline_ids,
        catalog_gateway_ids=catalog_gateway_ids,
        catalog_arrivals=catalog_arrivals,
        membership_count=membership_count,
        membership_seq=membership_seq,
    )


def membership_ready(
    window: SettleWindow,
    *,
    snapshot_gateway_ids: Iterable[str] | None,
    snapshot_pipeline_ids: Iterable[str] | None = None,
    land_paths: Iterable[str] = (),
    land_deleted_paths: Iterable[str] = (),
    pipeline_sources: Mapping[str, Iterable[str]] | None = None,
    step_type_modules: Mapping[str, str] | None = None,
    pipeline_step_types: Mapping[str, Iterable[str]] | None = None,
) -> bool:
    """R1 gateway-set equality, then tag, then per-gateway catalog arrival, then R3.

    An empty snapshot is never ready. An untagged membership is never ready.
    Ordering is arrival order on the folded window, not ``seq``.
    """
    snap = [str(item) for item in (snapshot_gateway_ids or ())]
    if not snap:
        return False
    if window.post_gateway_ids is None or not gateway_id_sets_equal(
        snap, window.post_gateway_ids
    ):
        return False
    if window.catalog_gateway_ids is None:
        return False
    if not set(snap) <= set(window.catalog_gateway_ids):
        return False
    if not set(snap) <= window.catalog_arrivals:
        return False
    snap_pipes = set(snapshot_pipeline_ids or ())
    sources = pipeline_sources or {}
    affected = affected_pipeline_ids(
        snap_pipes,
        land_paths,
        pipeline_sources=sources,
        step_type_modules=step_type_modules or {},
        pipeline_step_types=pipeline_step_types or {},
    )
    expected_absent = expected_absent_pipeline_ids(
        snap_pipes,
        land_deleted_paths,
        pipeline_sources=sources,
    )
    unrelated = snap_pipes - affected - expected_absent
    present = set(window.post_pipeline_ids or ())
    if unrelated - present:
        return False
    return True


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
    land_deleted_paths: Iterable[str] = (),
    pipeline_sources: Mapping[str, Iterable[str]] | None = None,
    step_type_modules: Mapping[str, str] | None = None,
    pipeline_step_types: Mapping[str, Iterable[str]] | None = None,
    op_run_failures: Iterable[str] = (),
) -> SettleVerdict:
    """Return the settle verdict. Cap expiry is never fail.

    ``latest_catalog_seq`` is legacy for direct callers. :func:`judge_event_window`
    passes ``None`` and does not derive a seq from the window.
    """
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
    sources = pipeline_sources or {}
    expected_absent = expected_absent_pipeline_ids(
        snap_pipes,
        land_deleted_paths,
        pipeline_sources=sources,
    )
    affected = affected_pipeline_ids(
        snap_pipes,
        land_paths,
        pipeline_sources=sources,
        step_type_modules=step_type_modules or {},
        pipeline_step_types=pipeline_step_types or {},
    )
    failures = set(op_run_failures)
    missing_unrelated = (snap_pipes - post_pipes) - affected - expected_absent
    if missing_unrelated:
        return "indeterminate"
    for pipeline_id in affected:
        if pipeline_id in expected_absent and pipeline_id not in post_pipes:
            continue
        if pipeline_id not in post_pipes or pipeline_id in failures:
            return "fail_attributable"
    return "pass"


def judge_event_window(
    events: Iterable[Mapping[str, Any]],
    *,
    snapshot_gateway_ids: Iterable[str] | None,
    snapshot_pipeline_ids: Iterable[str] | None,
    timed_out: bool,
    land_paths: Iterable[str] = (),
    land_deleted_paths: Iterable[str] = (),
    pipeline_sources: Mapping[str, Iterable[str]] | None = None,
    step_type_modules: Mapping[str, str] | None = None,
    pipeline_step_types: Mapping[str, Iterable[str]] | None = None,
    op_run_failures: Iterable[str] = (),
) -> SettleVerdict:
    """Fold a post-restart event window into :func:`judge_settle`.

    Cap expiry is indeterminate. A window :func:`membership_ready` rejects is
    indeterminate, including when the stream ended early. Once ready, the only
    remaining verdicts are pass and fail_attributable.
    """
    if timed_out:
        return "indeterminate"
    window = fold_settle_window(events)
    if not membership_ready(
        window,
        snapshot_gateway_ids=snapshot_gateway_ids,
        snapshot_pipeline_ids=snapshot_pipeline_ids,
        land_paths=land_paths,
        land_deleted_paths=land_deleted_paths,
        pipeline_sources=pipeline_sources,
        step_type_modules=step_type_modules,
        pipeline_step_types=pipeline_step_types,
    ):
        return "indeterminate"
    return judge_settle(
        snapshot_gateway_ids=snapshot_gateway_ids,
        snapshot_pipeline_ids=snapshot_pipeline_ids,
        post_gateway_ids=window.post_gateway_ids,
        post_pipeline_ids=window.post_pipeline_ids,
        membership_seq=window.membership_seq,
        latest_catalog_seq=None,
        timed_out=False,
        land_paths=land_paths,
        land_deleted_paths=land_deleted_paths,
        pipeline_sources=pipeline_sources,
        step_type_modules=step_type_modules,
        pipeline_step_types=pipeline_step_types,
        op_run_failures=op_run_failures,
    )


__all__ = [
    "CATALOG_CHANGED",
    "MEMBERSHIP_CATALOG_GATEWAYS_KEY",
    "MEMBERSHIP_SIGNAL",
    "PROGRESS_SIGNALS",
    "REACHABILITY_RESTORED",
    "SETTLE_CAP_S",
    "SettleVerdict",
    "SettleWindow",
    "StopReason",
    "affected_pipeline_ids",
    "expected_absent_pipeline_ids",
    "fold_settle_window",
    "gateway_id_sets_equal",
    "judge_event_window",
    "judge_settle",
    "membership_ready",
]
