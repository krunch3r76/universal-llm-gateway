"""``skills=`` survives the cursor-sdk generate fork all the way to the GIW payload.

Regression target: ``body.skills`` was parsed into ``TeamDispatchGenerateBody`` and
then never read, because ``seat=cursor-sdk`` forks out of the route before
``build_dispatch_body`` runs. The drop was silent — the dispatch succeeded while
ignoring every requested skill.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from systems.frontier_consult.cursor_sdk_prepared_handle import (
    PreparedCursorSdkHandle,
    handle_from_dict,
    handle_to_dict,
)
from systems.frontier_consult.cursor_sdk_skills import resolve_cursor_sdk_skills

_LIFE_SLUG = "prose-discipline"
_PLUGIN_SLUG = "reasoning-posture"


def _hub(tmp_path: Path, *specs: tuple[str, str]) -> Path:
    root = tmp_path / "hub"
    for relpath, slug in specs:
        path = root / relpath / slug / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\nname: {slug}\n---\n", encoding="utf-8")
    return root


@pytest.fixture
def hub_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = _hub(
        tmp_path,
        (".claude/skills", _LIFE_SLUG),
        ("cursor-plugins/ulg-ecosystem/skills", _PLUGIN_SLUG),
    )
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_skills.default_workspaces_root",
        lambda: root,
    )
    return root


@pytest.mark.offline
def test_resolvable_skills_return_canonical_slugs(hub_root: Path) -> None:
    events: list[object] = []

    resolved = resolve_cursor_sdk_skills(
        [_LIFE_SLUG, _PLUGIN_SLUG],
        request_id="req-1",
        role="cursor-sdk",
        resolved_model="cursor/composer-2.5",
        event_publisher=events.append,
    )

    assert resolved == (_LIFE_SLUG, _PLUGIN_SLUG)
    assert len(events) == 1


@pytest.mark.offline
def test_channel_resolved_event_reports_sot_layer(hub_root: Path) -> None:
    events: list = []

    resolve_cursor_sdk_skills(
        [_LIFE_SLUG, _PLUGIN_SLUG],
        request_id="req-1",
        role="cursor-sdk",
        resolved_model="cursor/composer-2.5",
        event_publisher=events.append,
    )

    payload = events[0].payload
    assert payload["role"] == "cursor-sdk"
    layers = {row["requested_id"]: row["sot_layer"] for row in payload["skills"]}
    assert layers == {_LIFE_SLUG: "life_local", _PLUGIN_SLUG: "plugin"}
    assert all(row["channel"] == "layer_b" for row in payload["skills"])


@pytest.mark.offline
def test_unresolvable_slug_fails_the_admit(hub_root: Path) -> None:
    """The whole point: a slug with no body is a refusal, not a silent no-op."""
    from systems.frontier_consult.admission import FrontierEndpointError

    with pytest.raises(FrontierEndpointError) as excinfo:
        resolve_cursor_sdk_skills(
            [_LIFE_SLUG, "definitely-not-a-skill"],
            request_id="req-1",
            role="cursor-sdk",
            resolved_model="cursor/composer-2.5",
            event_publisher=lambda _e: None,
        )

    exc = excinfo.value
    assert exc.status_code == 422
    assert exc.code == "skills_cursor_unresolvable"
    assert exc.details["skills"] == ["definitely-not-a-skill"]


@pytest.mark.offline
def test_empty_skills_resolves_to_empty_without_events(hub_root: Path) -> None:
    events: list[object] = []

    for empty in (None, [], ["", "  "]):
        assert (
            resolve_cursor_sdk_skills(
                empty,
                request_id="req-1",
                role="cursor-sdk",
                resolved_model="cursor/composer-2.5",
                event_publisher=events.append,
            )
            == ()
        )
    assert events == []


@pytest.mark.offline
def test_handle_round_trips_skills_through_the_proposal_store() -> None:
    """Queued dispatches are checkpointed as dicts; skills must survive promote."""
    handle = PreparedCursorSdkHandle(
        request_id="req-1",
        execution_id="exec-1",
        dispatch_id="d-1",
        thread_id="9803",
        resolved_model="cursor/composer-2.5",
        role="cursor-sdk",
        family="cursor",
        platform="cursor",
        to_agent="cursor-sdk",
        handoff_contract="light-bounded",
        packet_path="p.md",
        message=None,
        caller_agent="cursor",
        read_only=False,
        aligned_knobs=None,
        prompt_preamble=None,
        thread_subject="s",
        pointer_body="b",
        effective_bus_lifecycle="ephemeral",
        parent_dispatch_thread_id=None,
        dispatch_thread_id=None,
        density_triage=None,
        review_opt_out_reason_code=None,
        auto_review_child=False,
        auto_review_defaulted=False,
        claimed_via_atomic=False,
        admitted=True,
        alignment_warnings=(),
        knob_resolution=(),
        skills=(_LIFE_SLUG, _PLUGIN_SLUG),
    )

    assert handle_to_dict(handle)["skills"] == [_LIFE_SLUG, _PLUGIN_SLUG]
    assert handle_from_dict(handle_to_dict(handle)).skills == (
        _LIFE_SLUG,
        _PLUGIN_SLUG,
    )


@pytest.mark.offline
def test_handle_defaults_to_no_skills() -> None:
    """Legacy checkpoints predate the field."""
    payload = {
        "request_id": "req-1",
        "execution_id": "exec-1",
        "dispatch_id": "d-1",
        "thread_id": None,
        "resolved_model": "cursor/composer-2.5",
        "role": "cursor-sdk",
        "family": "cursor",
        "platform": "cursor",
        "to_agent": "cursor-sdk",
        "handoff_contract": "light-bounded",
        "thread_subject": "s",
        "pointer_body": "b",
    }

    assert handle_from_dict(payload).skills == ()


@pytest.mark.offline
def test_worker_payload_carries_skills() -> None:
    """``CursorDispatchRequest`` is ``extra=forbid``; the field must really exist."""
    from services.git_integration_worker.models.cursor_api import (
        CursorDispatchRequest,
    )

    req = CursorDispatchRequest(
        thread_id="9803",
        model="cursor/composer-2.5",
        dispatch_id="d-1",
        execution_id="exec-1",
        packet_path="p.md",
        skills=[_LIFE_SLUG],
    )

    assert req.skills == [_LIFE_SLUG]
    assert CursorDispatchRequest(
        thread_id="9803",
        model="cursor/composer-2.5",
        dispatch_id="d-1",
        execution_id="exec-1",
        packet_path="p.md",
    ).skills is None
