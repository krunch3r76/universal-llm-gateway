"""G1 — post-window Frictions audit fixtures."""

from __future__ import annotations

from scripts.model_manager.ui.controller.charter_runner import (
    frictions_window_audit as audit,
)


def _assertions(store: dict[int, dict]):
    def _get(aid: int) -> dict:
        return store.get(aid, {"error": "missing"})

    return _get


def _frictions(rows: list[dict]):
    def _list(**kwargs: object) -> dict:
        root = kwargs.get("charter_root")
        window = kwargs.get("window_index")
        out = []
        for row in rows:
            attrs = row.get("attributes") or {}
            if root and str(attrs.get("charter_root")) != str(root):
                continue
            if window is not None and attrs.get("window_index") != window:
                continue
            out.append(row)
        return {"items": out}

    return _list


def _checkpoint(*, frictions: str, subject: str = "CHECKPOINT — window 2") -> str:
    return f"""# {subject}

## Steps
1. [ ] Example

## Frictions
{frictions}

## Sidecars
_None this window._

## Next-pickup
- G1 — example

## WIP / In-flight
_None this window._
"""


def test_silence_without_defect_passes() -> None:
    body = _checkpoint(frictions="_None this window._")
    result = audit.audit_window_frictions(
        checkpoint_body=body,
        root_id="5624",
        window_index=2,
        assertion_get=_assertions({}),
        frictions=_frictions([]),
    )
    assert result.applicable
    assert not result.audit_failed
    assert result.section_class == "silence"


def test_fake_id_fails() -> None:
    body = _checkpoint(frictions="- [filed assertion:999] protocol: fake")
    result = audit.audit_window_frictions(
        checkpoint_body=body,
        root_id="5624",
        window_index=2,
        assertion_get=_assertions({}),
        frictions=_frictions([]),
    )
    assert result.audit_failed
    assert result.audit_failure_class == "unresolved_id"


def test_missing_section_fails() -> None:
    body = """# CHECKPOINT

## Steps
1. [ ] Example
"""
    result = audit.audit_window_frictions(
        checkpoint_body=body,
        root_id="5624",
        window_index=2,
        assertion_get=_assertions({}),
        frictions=_frictions([]),
    )
    assert result.audit_failed
    assert result.audit_failure_class == "missing_section"


def test_matching_row_passes() -> None:
    store = {
        42: {
            "id": 42,
            "attributes": {
                "charter_root": "5624",
                "window_index": 2,
                "actionable": True,
            },
        }
    }
    body = _checkpoint(frictions="- [filed assertion:42] protocol: real defect")
    result = audit.audit_window_frictions(
        checkpoint_body=body,
        root_id="5624",
        window_index=2,
        assertion_get=_assertions(store),
        frictions=_frictions([store[42]]),
    )
    assert not result.audit_failed


def test_uncited_actionable_non_fatal() -> None:
    store = {
        7: {
            "id": 7,
            "attributes": {
                "charter_root": "5624",
                "window_index": 2,
                "actionable": True,
            },
        }
    }
    body = _checkpoint(frictions="_None this window._")
    result = audit.audit_window_frictions(
        checkpoint_body=body,
        root_id="5624",
        window_index=2,
        assertion_get=_assertions(store),
        frictions=_frictions([store[7]]),
        worker_turns=[
            {
                "turn_number": 3,
                "body": '{"status":"partial","deviations":["probe:failed"]}',
            }
        ],
    )
    assert not result.audit_failed
    assert result.audit_failure_class == "filed_uncited"
    assert 7 in result.uncited_ids


def test_not_applicable_on_timeout() -> None:
    body = _checkpoint(frictions="_None this window._")
    result = audit.audit_window_frictions(
        checkpoint_body=body,
        root_id="5624",
        window_index=2,
        assertion_get=_assertions({}),
        frictions=_frictions([]),
        worker_closeout_status="timeout",
    )
    assert not result.applicable
    assert result.not_applicable_reason == "closeout_timeout"


def test_all_non_actionable_ceremonial_with_defect() -> None:
    store = {
        5: {
            "id": 5,
            "attributes": {
                "charter_root": "5624",
                "window_index": 2,
                "actionable": False,
            },
        }
    }
    body = _checkpoint(frictions="- [filed assertion:5] protocol: informational")
    result = audit.audit_window_frictions(
        checkpoint_body=body,
        root_id="5624",
        window_index=2,
        assertion_get=_assertions(store),
        frictions=_frictions([store[5]]),
        gate_bypass_count=1,
    )
    assert result.ceremonial_suspected
    assert result.audit_failed
    assert result.audit_failure_class == "ceremonial_suspected"


def test_capture_noise_deviations_do_not_trigger_ceremonial() -> None:
    """6110-w3 shape: status=complete + capture/degraded noise + silence → pass."""
    body = _checkpoint(frictions="_None this window._")
    result = audit.audit_window_frictions(
        checkpoint_body=body,
        root_id="6110",
        window_index=3,
        assertion_get=_assertions({}),
        frictions=_frictions([]),
        worker_closeout_status="complete",
        worker_turns=[
            {
                "turn_number": 2,
                "body": (
                    '{"status":"complete","deviations":['
                    '"capture:outside_repo_paths_present",'
                    '"stream_only_effect",'
                    '"degraded:sdk_git_probe_absent",'
                    '"capture:non_file_manifest_entry_dropped"'
                    "]}"
                ),
            }
        ],
    )
    assert result.applicable
    assert not result.ceremonial_suspected
    assert not result.audit_failed
    assert result.section_class == "silence"


def test_material_deviation_still_triggers_ceremonial_on_silence() -> None:
    body = _checkpoint(frictions="_None this window._")
    result = audit.audit_window_frictions(
        checkpoint_body=body,
        root_id="6110",
        window_index=3,
        assertion_get=_assertions({}),
        frictions=_frictions([]),
        worker_closeout_status="complete",
        worker_turns=[
            {
                "turn_number": 2,
                "body": '{"status":"complete","deviations":["probe:failed"]}',
            }
        ],
    )
    assert result.ceremonial_suspected
    assert result.audit_failed
    assert result.audit_failure_class == "ceremonial_suspected"


def test_closed_same_window_citation_is_not_phantom() -> None:
    """6110-w4 shape: cite filed assertion after friction_close → not unresolved_id."""
    store = {
        26616: {
            "id": 26616,
            "attributes": {
                "charter_root": "6110",
                "window_index": 4,
                "actionable": True,
            },
            "superseded_by": 26617,
            "valid_until": "2026-07-28T03:19:32Z",
        }
    }
    body = _checkpoint(
        frictions=(
            "- [filed assertion:26616] protocol: ceremonial false-positive "
            "(closed a:26617)"
        ),
        subject="CHECKPOINT wave 4 — G3 close + ceremonial capture-noise fix",
    )
    result = audit.audit_window_frictions(
        checkpoint_body=body,
        root_id="6110",
        window_index=4,
        assertion_get=_assertions(store),
        # Live list excludes superseded rows (op default superseded=False).
        frictions=_frictions([]),
        worker_closeout_status="complete",
    )
    assert result.applicable
    assert 26616 in result.cited_ids
    assert 26616 not in result.phantom_ids
    assert 26616 not in result.unresolved_ids
    assert not result.resolved_actionable_rows
    assert not result.audit_failed
