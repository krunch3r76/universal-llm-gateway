"""Tests for navigator CDP transport clauses and self-expiring wire guards."""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from bus_watch.fable_lock import WATCH_DIR
from bus_watch.navigator_wake import (
    DIGEST_SOURCE_EXPR,
    acquire_navigator_lease,
    evaluate_navigator_wake,
    fire_navigator_wake,
    navigator_lock_path,
    read_navigator_lock,
    render_navigator_doorbell,
)


def _digest(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "ts": "2026-09-14T07:00:00Z",
        "fingerprint": "abc123def4567890",
        "night_id": "2026-09-14",
        "root": {"id": "10479", "slug": "claude-ai-navigator-seat"},
        "lanes": [{"id": "10532", "turns": 3, "status": "active", "lifecycle": "open"}],
        "attention": [{"id": "10586", "unread": 1, "last_subject": "DIGEST 10479"}],
        "policy": {
            "navigator_model": "cdp/opus-5-high",
            "navigator_model_source": "override",
            "max_navigator_commissions_per_night": 0,
            "navigator_grace_seconds": 900,
        },
        "register": "autonomous",
    }
    base.update(over)
    return base


def _state(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "last_induction_at": 0.0,
        "policy": {"navigator_model": "cdp/opus-5-high"},
    }
    base.update(over)
    return base


@pytest.mark.offline
def test_clause1d_in_force() -> None:
    """1c→1d transition (T1 parent_thread wire, lane-11192, 2026-09-14)."""
    from stargate_dispatch.client import _ALLOWED_FIELDS

    assert "parent_thread" in _ALLOWED_FIELDS
    assert "purpose" not in _ALLOWED_FIELDS


@pytest.mark.offline
def test_admission_still_stubbed() -> None:
    """DESIGNED TO FAIL when evaluate_new_admission enforces — rewrite clause 1 per 1d."""
    from cdp_ask.lane_admission import evaluate_new_admission

    admit, label = evaluate_new_admission(
        "mission", seat_count=99, other_count=99, unattended=True
    )
    assert admit is True
    assert label is None


@pytest.mark.offline
def test_navigator_commission_cap_fail_closed_default() -> None:
    evaluation = evaluate_navigator_wake(
        _digest(), _state(), register="autonomous", now=10_000.0
    )
    assert evaluation["include_commission"] is False
    assert "commission:" not in evaluation["doorbell"]


@pytest.mark.offline
def test_navigator_grace_blocks_until_elapsed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("bus_watch.navigator_wake.WATCH_DIR", tmp_path)
    state = _state(last_induction_at=9_500.0)
    evaluation = evaluate_navigator_wake(
        _digest(), state, register="autonomous", now=10_000.0
    )
    assert evaluation["clauses"]["navigator_grace_elapsed"] is False
    assert evaluation["skip_reason"] == "grace_not_elapsed"


@pytest.mark.offline
def test_navigator_single_flight_lease(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("bus_watch.navigator_wake.WATCH_DIR", tmp_path)
    first = acquire_navigator_lease("10479", ttl_seconds=600.0)
    assert first["ok"] is True
    second = acquire_navigator_lease("10479", ttl_seconds=600.0)
    assert second["ok"] is False
    assert second["reason"] == "navigator_in_flight"
    evaluation = evaluate_navigator_wake(
        _digest(), _state(), register="autonomous", now=10_000.0
    )
    assert evaluation["clauses"]["navigator_single_flight"] is False


@pytest.mark.offline
def test_navigator_lease_concurrent_acquire_one_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression a:33951 — flock must block two simultaneous acquire attempts."""
    monkeypatch.setattr("bus_watch.navigator_wake.WATCH_DIR", tmp_path)
    barrier = threading.Barrier(2)
    results: list[dict[str, Any]] = []

    def _try() -> None:
        barrier.wait()
        results.append(acquire_navigator_lease("10479", ttl_seconds=600.0))

    threads = [threading.Thread(target=_try) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 2
    winners = [r for r in results if r.get("ok")]
    losers = [r for r in results if not r.get("ok")]
    assert len(winners) == 1
    assert len(losers) == 1
    assert losers[0]["reason"] == "navigator_in_flight"


@pytest.mark.offline
def test_register_inversion() -> None:
    auto = evaluate_navigator_wake(
        _digest(), _state(), register="autonomous", now=10_000.0
    )
    attended = evaluate_navigator_wake(
        _digest(), _state(), register="attended", now=10_000.0
    )
    assert auto["clauses"]["register_not_attended"] is True
    assert attended["clauses"]["register_not_attended"] is False


@pytest.mark.offline
def test_navigator_model_bound_rejects_gear_preset() -> None:
    digest = _digest(
        policy={
            "navigator_model": "cdp/opus-5-high",
            "navigator_model_source": "gear_preset",
            "max_navigator_commissions_per_night": 0,
        }
    )
    evaluation = evaluate_navigator_wake(
        digest, _state(), register="autonomous", now=10_000.0
    )
    assert evaluation["clauses"]["navigator_model_bound"] is False


@pytest.mark.offline
def test_fire_navigator_body_carries_parent_thread(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("bus_watch.navigator_wake.WATCH_DIR", tmp_path)
    result = fire_navigator_wake(
        "10479",
        _digest(),
        _state(),
        register="autonomous",
        dry_run=True,
    )
    assert result["body"]["parent_thread"] == "10479"
    assert result["evaluation"]["clauses"]["navigator_lane_bound"] is True


@pytest.mark.offline
def test_fire_navigator_body_carries_surface_derived_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("bus_watch.navigator_wake.WATCH_DIR", tmp_path)
    result = fire_navigator_wake(
        "10479",
        _digest(),
        _state(),
        register="autonomous",
        dry_run=True,
    )
    assert result["body"]["skills"] == ["liaison", "reasoning-posture"]
    doorbell = result["evaluation"]["doorbell"]
    assert "Use the liaison skill." in doorbell
    assert "Use the reasoning-posture skill." in doorbell


@pytest.mark.offline
def test_fire_navigator_dry_run_submits_nothing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("bus_watch.navigator_wake.WATCH_DIR", tmp_path)

    def _boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("submit must not run on dry_run")

    result = fire_navigator_wake(
        "10479",
        _digest(),
        _state(),
        register="autonomous",
        dry_run=True,
        submit=_boom,
    )
    assert result["dry_run"] is True
    assert result["ok"] is True
    assert "clauses" in str(result.get("evaluation"))


@pytest.mark.offline
def test_fire_navigator_records_commission_counter(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("bus_watch.navigator_wake.WATCH_DIR", tmp_path)
    state = _state()
    digest = _digest(
        policy={
            "navigator_model": "cdp/opus-5-high",
            "navigator_model_source": "override",
            "max_navigator_commissions_per_night": 2,
        }
    )

    def _submit(body):  # noqa: ANN001
        return {"execution_id": "exec-1", "thread_id": "11165"}, 200

    result = fire_navigator_wake(
        "10479",
        digest,
        state,
        register="autonomous",
        dry_run=False,
        submit=_submit,
    )
    assert result["ok"] is True
    assert state["navigator_commissions_by_night"]["2026-09-14"] == 1
    lock = read_navigator_lock("10479")
    assert lock.get("execution_id") == "exec-1"


@pytest.mark.offline
def test_navigator_lock_under_watch_dir() -> None:
    path = navigator_lock_path("10479")
    assert path.parent == WATCH_DIR
    assert path.name == "navigator-10479.lock"


def _load_liaison_induce():
    repo = Path(__file__).resolve().parents[2]
    path = repo / "scripts" / "liaison-induce.py"
    spec = importlib.util.spec_from_file_location("liaison_induce", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.offline
def test_liaison_induce_reports_navigator_transport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Non-attended register selects navigator transport on --dry-run."""
    induce = _load_liaison_induce()
    monkeypatch.setattr(induce, "WATCH_DIR", tmp_path)
    monkeypatch.setattr("bus_watch.fable_lock.WATCH_DIR", tmp_path)
    monkeypatch.setattr("bus_watch.navigator_wake.WATCH_DIR", tmp_path)
    state_path = tmp_path / "liaison-10479.tick.json"
    state_path.write_text(
        json.dumps(
            {
                "register": "autonomous",
                "policy": {
                    "navigator_model": "cdp/opus-5-high",
                    "navigator_model_source": "override",
                },
            }
        ),
        encoding="utf-8",
    )
    digest = _digest()
    digest["induction"] = "WAKE stub"
    monkeypatch.setattr(induce, "build_digest", lambda *a, **k: digest)
    monkeypatch.setattr(
        sys, "argv", ["liaison-induce.py", "--root", "10479", "--dry-run"]
    )
    rc = induce.main()
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["transport"] == "navigator"
    assert payload["clauses"] is not None
    assert "doorbell" in payload


@pytest.mark.offline
def test_liaison_induce_reports_ide_transport(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    induce = _load_liaison_induce()
    monkeypatch.setattr(
        induce,
        "load_state",
        lambda _p: {"register": "attended"},
    )
    digest = _digest(register="attended")
    digest["induction"] = "WAKE stub"
    monkeypatch.setattr(induce, "build_digest", lambda *a, **k: digest)
    monkeypatch.setattr(
        induce,
        "fire_ide_followup",
        lambda *a, **k: {"ok": True, "dry_run": True},
    )
    monkeypatch.setattr(
        sys, "argv", ["liaison-induce.py", "--root", "10479", "--dry-run"]
    )
    rc = induce.main()
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["transport"] == "ide"


def test_digest_echo_source_is_independent_of_epoch() -> None:
    """`source` must name the producing expression, not repeat the fingerprint.

    Regression for the R20 residual: `render_navigator_doorbell` passed the
    digest fingerprint as BOTH `digest_source` and `fingerprint`, so the status
    quintuple advertised five fields while carrying four independent ones. A
    reader could not tell where the echoed value came from.
    """
    digest = _digest()
    fp = digest["fingerprint"]
    text = render_navigator_doorbell(digest, root_id="10479", include_commission=False)

    assert f"source={DIGEST_SOURCE_EXPR}" in text
    assert f"epoch={fp}" in text
    assert f"source={fp}" not in text
    assert DIGEST_SOURCE_EXPR != fp


def test_digest_echo_source_names_the_attention_filter() -> None:
    """The source expression must disclose the kinds excluded from the value.

    `_attention_row_ids` drops `budget_estimate` and `friction` rows, so a bare
    `digest.attention[].id` would overstate what the echoed value covers.
    """
    digest = _digest(
        attention=[
            {"id": "10586", "unread": 1},
            {"id": "99999", "kind": "friction"},
            {"id": "99998", "kind": "budget_estimate"},
        ]
    )
    text = render_navigator_doorbell(digest, root_id="10479", include_commission=False)

    assert "value=10586" in text
    assert "99999" not in text
    assert "99998" not in text
    for kind in ("budget_estimate", "friction"):
        assert kind in DIGEST_SOURCE_EXPR
