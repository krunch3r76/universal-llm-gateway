"""Test support for house policy and conductor packet isolation (not imported in production)."""

from __future__ import annotations

import dataclasses
import importlib
import json
import re
from pathlib import Path
from typing import Any

from implement_admission.impresario_policy import (
    ImpresarioPolicy,
    ResolvedRoles,
    SeatSpec,
    resolve_roles,
)

HOUSE_MODEL = "anthropic/claude-sonnet-5-5"
_CDP_PIN = "cdp/fable-5.1"
_PIN_EFFORT = "high"

PIN_CRITIC = SeatSpec(
    model=_CDP_PIN,
    effort_rung=_PIN_EFFORT,
    transport="cdp_parked",
)
PIN_SKETCH = SeatSpec(
    model=_CDP_PIN,
    effort_rung=_PIN_EFFORT,
    transport="cdp_parked",
)
PIN_SKEPTIC = SeatSpec(
    model=_CDP_PIN,
    effort_rung=_PIN_EFFORT,
    transport="cdp_parked",
)
PIN_REVIEW = SeatSpec(
    model=_CDP_PIN,
    effort_rung=_PIN_EFFORT,
    transport="cdp_parked",
)
PIN_DRIVER = SeatSpec(
    model="cursor/composer-2.5",
    effort_rung="medium",
    transport="cursor_nested",
    seat="cursor-sdk",
)
PIN_MODELS: tuple[str, ...] = (_CDP_PIN,)

SEAM_MODULES: tuple[str, ...] = (
    "implement_admission.conductor_critic_render",
    "implement_admission.conductor_packet_render",
    "implement_admission.conductor_task_guidance",
    "implement_admission.conductor_policy_snapshot",
)

R2_STAMP_RE = re.compile(
    r"model=\S+ \(impresario\.critic; do not substitute cdp/\)"
)


def _pinned_policy(live: ImpresarioPolicy) -> ImpresarioPolicy:
    return dataclasses.replace(
        live,
        critic=PIN_CRITIC,
        driver=PIN_DRIVER,
    )


def pin_cdp_roles(monkeypatch: Any) -> ResolvedRoles:
    live = resolve_roles()
    roles = dataclasses.replace(
        live,
        driver=PIN_DRIVER,
        critic=PIN_CRITIC,
        sketch=PIN_SKETCH,
        skeptic=PIN_SKEPTIC,
        review=PIN_REVIEW,
    )
    pinned_pol = _pinned_policy(live.policy)

    def _resolve() -> ResolvedRoles:
        return roles

    def _load() -> ImpresarioPolicy:
        return pinned_pol

    import implement_admission.impresario_policy as ip_mod

    monkeypatch.setattr(ip_mod, "resolve_roles", _resolve)
    monkeypatch.setattr(ip_mod, "load_impresario_policy", _load)
    for mod_name in SEAM_MODULES:
        mod = importlib.import_module(mod_name)
        monkeypatch.setattr(mod, "resolve_roles", _resolve, raising=False)
        monkeypatch.setattr(mod, "load_impresario_policy", _load, raising=False)
    return roles


def isolate_watch_dir(monkeypatch: Any, tmp_path: Path) -> Path:
    watch = tmp_path / "watchers"
    watch.mkdir(parents=True, exist_ok=True)
    import bus_watch.fable_lock as fl_mod

    monkeypatch.setattr(fl_mod, "WATCH_DIR", watch)
    return watch


def write_tick(
    watch_dir: Path,
    thread_id: str,
    policy: dict[str, Any] | None,
    **extra: Any,
) -> Path:
    path = watch_dir / f"liaison-{thread_id}.tick.json"
    state: dict[str, Any] = dict(extra)
    if policy is not None:
        state["policy"] = policy
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return path


def strip_binds_block(text: str) -> str:
    start_key = "House binds (summoning thread"
    end_key = "House binds end."
    start = text.find(start_key)
    if start < 0:
        return text
    end = text.find(end_key, start)
    if end < 0:
        return text
    end += len(end_key)
    if end < len(text) and text[end] == "\n":
        end += 1
    return text[:start] + text[end:]


def _span(text: str, open_tag: str, close_tag: str) -> tuple[int, int] | None:
    m_open = re.search(re.escape(open_tag), text, re.DOTALL)
    if not m_open:
        return None
    start = m_open.start()
    m_close = re.search(re.escape(close_tag), text[start:], re.DOTALL)
    if not m_close:
        return None
    return start, start + m_close.end()


def strip_r2_stamps(text: str) -> tuple[str, int, int]:
    tip_span = _span(text, "<scoreboard_tip", "</scoreboard_tip>")
    scope_span = _span(text, "<scope>", "</scope>")
    n_tip = 0
    n_scope = 0
    parts: list[str] = []
    last = 0
    for m in R2_STAMP_RE.finditer(text):
        parts.append(text[last : m.start()])
        pos = m.start()
        if tip_span and tip_span[0] <= pos < tip_span[1]:
            n_tip += 1
            last = m.end()
        elif scope_span and scope_span[0] <= pos < scope_span[1]:
            n_scope += 1
            last = m.end()
        else:
            parts.append(m.group(0))
            last = m.end()
    parts.append(text[last:])
    return "".join(parts), n_tip, n_scope


def assert_house_packet_clean(text: str) -> None:
    assert (
        "House binds (summoning thread" in text
        or "House policy unresolved for summoning thread" in text
    )
    rem, n_tip, n_scope = strip_r2_stamps(strip_binds_block(text))
    assert re.search(r"cdp/[A-Za-z0-9]", rem) is None
    for pin in PIN_MODELS:
        assert pin not in rem
    assert n_tip <= 1
    assert n_scope <= text.count(" CLAIMED:")
