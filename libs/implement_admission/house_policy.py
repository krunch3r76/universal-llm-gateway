"""House tick policy read and packet bind rendering (phase 1)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from bus_watch.tick_state import load_state
from implement_admission.impresario_policy import (
    ResolvedRoles,
    seat_transport_for_model,
    with_seat_model,
)

if TYPE_CHECKING:
    from implement_admission.conductor_context import RematerializeContext

_log = logging.getLogger(__name__)

HOUSE_BINDS_MAX_LINES: int = 12
HOUSE_BIND_MAX_CHARS: int = 240

HousePolicySource = Literal["tick", "carrier", "unresolved", "none"]


@dataclass(frozen=True, slots=True)
class HousePolicy:
    binds: tuple[str, ...]
    critic_model: str | None
    critic_model_knobs: dict[str, Any] | None
    source: HousePolicySource
    as_of: str | None
    thread_id: str | None


def _watch_dir() -> Path:
    from bus_watch.fable_lock import WATCH_DIR

    return WATCH_DIR


def _parse_binds(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            return ()
        s = item.strip()
        if s:
            out.append(s)
    return tuple(out)


def _parse_critic_model(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    model = raw.strip()
    if not model:
        return None
    if seat_transport_for_model(model) is None:
        return None
    return model


def _parse_knobs(raw: Any) -> dict[str, Any] | None:
    return raw if isinstance(raw, dict) else None


def _sanitize_bind(bind: str) -> str:
    s = " ".join(bind.split())
    s = s.replace("</", "<\\/")
    if len(s) > HOUSE_BIND_MAX_CHARS:
        s = s[:239] + "…"
    return s


def _thread_label(thread_id: str | None) -> str:
    return thread_id if thread_id else "unknown"


def _policy_from_carriers(
    rematerialize: RematerializeContext | None,
) -> tuple[tuple[str, ...], str | None, dict[str, Any] | None] | None:
    if rematerialize is None:
        return None
    binds = rematerialize.house_induction_binds
    model = rematerialize.house_critic_model
    knobs = rematerialize.house_critic_model_knobs
    if binds or model or knobs:
        return binds, model, knobs
    return None


def _read_tick_policy(
    thread_id: str,
    watch_dir: Path,
) -> tuple[HousePolicySource, tuple[str, ...], str | None, dict[str, Any] | None, str | None]:
    path = watch_dir / f"liaison-{thread_id}.tick.json"
    try:
        state = load_state(path)
    except Exception:
        _log.exception("read_house_policy: load_state failed for %s", path)
        return "unresolved", (), None, None, None
    if not state:
        return "unresolved", (), None, None, None
    as_of: str | None = None
    try:
        if path.is_file():
            mtime = path.stat().st_mtime
            as_of = datetime.fromtimestamp(mtime, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except OSError:
        _log.exception("read_house_policy: stat failed for %s", path)
    policy_raw = state.get("policy")
    policy = policy_raw if isinstance(policy_raw, dict) else {}
    binds = _parse_binds(policy.get("induction_binds"))
    model = _parse_critic_model(policy.get("critic_model"))
    knobs = _parse_knobs(policy.get("critic_model_knobs"))
    return "tick", binds, model, knobs, as_of


def read_house_policy(
    summoning_thread_id: str | None,
    *,
    summoning_thread_id_unresolved: bool = False,
    rematerialize: RematerializeContext | None = None,
    watch_dir: Path | None = None,
) -> HousePolicy:
    """Resolve house binds and critic model; never raises."""
    try:
        wd = watch_dir if watch_dir is not None else _watch_dir()
        carriers = _policy_from_carriers(rematerialize)
        sid = str(summoning_thread_id or "").strip() or None

        if not sid:
            if carriers is not None:
                binds, model, knobs = carriers
                return HousePolicy(
                    binds=binds,
                    critic_model=model,
                    critic_model_knobs=knobs,
                    source="carrier",
                    as_of=None,
                    thread_id=None,
                )
            if summoning_thread_id_unresolved:
                return HousePolicy(
                    binds=(),
                    critic_model=None,
                    critic_model_knobs=None,
                    source="unresolved",
                    as_of=None,
                    thread_id=None,
                )
            return HousePolicy(
                binds=(),
                critic_model=None,
                critic_model_knobs=None,
                source="none",
                as_of=None,
                thread_id=None,
            )

        tick_source, binds, model, knobs, as_of = _read_tick_policy(sid, wd)
        if tick_source == "tick":
            return HousePolicy(
                binds=binds,
                critic_model=model,
                critic_model_knobs=knobs,
                source="tick",
                as_of=as_of,
                thread_id=sid,
            )
        if carriers is not None:
            c_binds, c_model, c_knobs = carriers
            return HousePolicy(
                binds=c_binds,
                critic_model=c_model,
                critic_model_knobs=c_knobs,
                source="carrier",
                as_of=None,
                thread_id=sid,
            )
        return HousePolicy(
            binds=(),
            critic_model=None,
            critic_model_knobs=None,
            source="unresolved",
            as_of=None,
            thread_id=sid,
        )
    except Exception:
        _log.exception("read_house_policy: unexpected failure")
        return HousePolicy(
            binds=(),
            critic_model=None,
            critic_model_knobs=None,
            source="unresolved",
            as_of=None,
            thread_id=str(summoning_thread_id or "").strip() or None,
        )


def with_house_critic(
    roles: ResolvedRoles,
    house_critic_model: str | None,
) -> ResolvedRoles:
    if house_critic_model is None:
        return roles
    return replace(
        roles,
        critic=with_seat_model(roles.critic, house_critic_model),
        sketch=with_seat_model(roles.sketch, house_critic_model),
        skeptic=with_seat_model(roles.skeptic, house_critic_model),
        review=with_seat_model(roles.review, house_critic_model),
    )


def roles_for_ctx(ctx: Any) -> ResolvedRoles:
    from implement_admission.impresario_policy import resolve_roles

    return with_house_critic(
        resolve_roles(),
        getattr(ctx, "house_critic_model", None),
    )


def render_house_lines(ctx: Any) -> tuple[str, ...]:
    source = getattr(ctx, "house_policy_source", "none") or "none"
    binds: tuple[str, ...] = getattr(ctx, "house_induction_binds", ()) or ()
    model: str | None = getattr(ctx, "house_critic_model", None)
    as_of: str | None = getattr(ctx, "house_policy_as_of", None)
    thread_id: str | None = getattr(ctx, "summoning_thread_id", None)

    if source == "none":
        return ()
    if source == "unresolved":
        return (
            f"House policy unresolved for summoning thread {_thread_label(thread_id)}; "
            "packet uses global route_policy roles.",
        )
    if source == "tick" and not binds and not model:
        return ()

    lines: list[str] = []
    as_of_disp = as_of if as_of else "none"
    lines.append(
        f"House binds (summoning thread {_thread_label(thread_id)}, "
        f"policy_source={source}, as_of={as_of_disp}):"
    )
    if model:
        lines.append(f"critic_model: {model}")
        lines.append(
            "R1 scoreboard row model stamps are global defaults; "
            "the house critic_model above governs."
        )
    shown = binds[:HOUSE_BINDS_MAX_LINES]
    for bind in shown:
        lines.append(f"- {_sanitize_bind(bind)}")
    overflow = len(binds) - HOUSE_BINDS_MAX_LINES
    if overflow > 0:
        if source == "carrier":
            lines.append(f"+{overflow} more binds (carrier source; not rendered)")
        else:
            lines.append(
                f"+{overflow} more binds in liaison-{_thread_label(thread_id)}.tick.json"
            )
    lines.append("House binds end.")
    return tuple(lines)
