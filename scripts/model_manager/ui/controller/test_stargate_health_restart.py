"""Falsifier for the stargate health-not-process restart rule."""

from scripts.model_manager.ui.controller.stargate_health_restart import (
    note_stargate_probe,
)


def test_running_resets_streak() -> None:
    streak, restart = note_stargate_probe(2, "running", uptime_s=120.0)
    assert streak == 0
    assert restart is False


def test_stopped_does_not_restart() -> None:
    streak, restart = note_stargate_probe(2, "stopped", uptime_s=None)
    assert streak == 0
    assert restart is False


def test_unhealthy_during_boot_grace_does_not_increment() -> None:
    streak, restart = note_stargate_probe(0, "unhealthy", uptime_s=20.0)
    assert streak == 0
    assert restart is False


def test_third_post_grace_miss_restarts() -> None:
    streak, restart = note_stargate_probe(0, "unhealthy", uptime_s=90.0)
    assert (streak, restart) == (1, False)
    streak, restart = note_stargate_probe(streak, "unhealthy", uptime_s=95.0)
    assert (streak, restart) == (2, False)
    streak, restart = note_stargate_probe(streak, "unhealthy", uptime_s=100.0)
    assert (streak, restart) == (3, True)


def test_unknown_uptime_does_not_restart() -> None:
    streak, restart = note_stargate_probe(2, "unhealthy", uptime_s=None)
    assert streak == 2
    assert restart is False


def test_timeout_detail_does_not_advance_or_restart() -> None:
    """The detail string ``check_stargate`` records for an httpx read timeout."""
    detail = "PID 1152034 (2h 3m), health probe failed: ReadTimeout"
    streak = 0
    for _ in range(5):
        streak, restart = note_stargate_probe(
            streak, "unhealthy", uptime_s=120.0, detail=detail
        )
        assert restart is False
    assert streak == 0


def test_timeout_holds_existing_hard_miss_streak() -> None:
    streak, restart = note_stargate_probe(
        2,
        "unhealthy",
        uptime_s=120.0,
        detail="PID 1, health probe failed: TimeoutError",
    )
    assert (streak, restart) == (2, False)
    streak, restart = note_stargate_probe(
        streak, "unhealthy", uptime_s=125.0, detail="PID 1, port not responding"
    )
    assert (streak, restart) == (3, True)


def test_connect_error_still_counts() -> None:
    streak, restart = note_stargate_probe(
        2,
        "unhealthy",
        uptime_s=120.0,
        detail="PID 1, health probe failed: ConnectError",
    )
    assert (streak, restart) == (3, True)
