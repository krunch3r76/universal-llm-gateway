"""Unit tests for the pure logic of the profile-keyed CDP lane allocator."""

from __future__ import annotations

import fcntl
import os
import threading
from pathlib import Path

import pytest

from claude_bundles import cdp_lane

pytestmark = pytest.mark.offline


@pytest.fixture
def isolated_lane_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    lane_dir = tmp_path / "cdp-lanes"
    lane_dir.mkdir()
    monkeypatch.setattr(cdp_lane, "LANE_DIR", lane_dir)
    monkeypatch.setattr(cdp_lane, "ALLOC_LOCK", lane_dir / "alloc.lock")
    return lane_dir


def test_resolve_suffix_base():
    assert cdp_lane.resolve_suffix("ask", fresh=False, taken=set()) == "ask"
    assert cdp_lane.resolve_suffix("fable", fresh=False, taken=set()) == "fable-consult"
    # unknown intent passes through as its own suffix
    assert cdp_lane.resolve_suffix("smoke", fresh=False, taken=set()) == "smoke"


def test_resolve_suffix_fresh_mints_next_index():
    assert cdp_lane.resolve_suffix("ask", fresh=True, taken=set()) == "ask-2"
    assert cdp_lane.resolve_suffix("ask", fresh=True, taken={"ask-2"}) == "ask-3"
    assert (
        cdp_lane.resolve_suffix("ask", fresh=True, taken={"ask-2", "ask-3"}) == "ask-4"
    )


def test_resolve_suffix_rejects_traversal():
    for bad in ("../evil", ".hidden", "a/b"):
        with pytest.raises(cdp_lane.LaneError):
            cdp_lane.resolve_suffix(bad, fresh=False, taken=set())


def test_profile_and_lock_paths():
    assert cdp_lane.profile_for("ask").name.endswith("-ask")
    assert cdp_lane.lock_path_for("ask").name == "ask.lock"


def test_select_free_port_picks_lowest_free():
    listening = {9223, 9224}
    port = cdp_lane.select_free_port(lambda p: p in listening, exclude=set())
    assert port == 9225


def test_select_free_port_honours_exclude():
    port = cdp_lane.select_free_port(lambda p: False, exclude={9223, 9224})
    assert port == 9225


def test_select_free_port_exhausted():
    with pytest.raises(cdp_lane.LaneError):
        cdp_lane.select_free_port(lambda p: True, exclude=set())


def test_cdp_display_prefers_cdp_display(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CDP_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    assert cdp_lane.cdp_display() == ":1"
    monkeypatch.setenv("DISPLAY", ":5")
    assert cdp_lane.cdp_display() == ":5"
    monkeypatch.setenv("CDP_DISPLAY", ":2")
    assert cdp_lane.cdp_display() == ":2"


def test_parse_chrome_lane_flattened():
    blob = (
        "/opt/google/chrome/chrome --remote-debugging-port=9223 "
        "--remote-allow-origins=* "
        "--user-data-dir=/home/k/.gateway/claude-ai-chrome-profile-ask "
        "--no-first-run"
    )
    port, udd = cdp_lane.parse_chrome_lane(blob)
    assert port == 9223
    assert udd.endswith("-ask")


def test_parse_chrome_lane_nul_separated():
    blob = "chrome\x00--remote-debugging-port=9230\x00--user-data-dir=/x/y\x00"
    port, udd = cdp_lane.parse_chrome_lane(blob)
    assert port == 9230
    assert udd == "/x/y"


def test_parse_chrome_lane_space_separated():
    blob = (
        "/opt/google/chrome/chrome --remote-debugging-port=9223 "
        "--user-data-dir /home/k/.gateway/claude-ai-chrome-profile-ask "
        "--no-first-run"
    )
    port, udd = cdp_lane.parse_chrome_lane(blob)
    assert port == 9223
    assert udd.endswith("-ask")


def test_parse_chrome_lane_absent():
    assert cdp_lane.parse_chrome_lane("chrome --type=renderer") == (None, None)


def test_seed_profile_rsync_excludes_optguide(tmp_path: Path) -> None:
    source = tmp_path / "primary"
    dest = tmp_path / "reg-profile"
    argv = cdp_lane.seed_profile_rsync_argv(source, dest)
    assert "--exclude=OptGuide*" in argv
    assert "--exclude=optimization_guide_model_store" in argv
    assert argv[-2:] == [f"{source}/", f"{dest}/"]


def test_chrome_launch_argv_disables_optimization_guide(tmp_path: Path) -> None:
    profile = tmp_path / "lane-profile"
    argv = cdp_lane.chrome_launch_argv(9225, profile)
    assert "--disable-features=OptimizationGuideOnDeviceModel" in argv
    assert f"--user-data-dir={profile}" in argv
    assert "--remote-debugging-port=9225" in argv
    assert "--disk-cache-size=134217728" in argv
    assert "--media-cache-size=134217728" in argv


def test_claim_fresh_profile_lock_skips_held_suffixes(
    isolated_lane_dir: Path,
) -> None:
    ask_fd = cdp_lane._open_lock(cdp_lane.lock_path_for("ask"))
    fcntl.flock(ask_fd, fcntl.LOCK_EX)
    ask2_fd = cdp_lane._open_lock(cdp_lane.lock_path_for("ask-2"))
    fcntl.flock(ask2_fd, fcntl.LOCK_EX)
    try:
        with cdp_lane._alloc_lock():
            fd, suffix = cdp_lane._claim_fresh_profile_lock("ask")
        assert suffix == "ask-3"
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    finally:
        fcntl.flock(ask2_fd, fcntl.LOCK_UN)
        os.close(ask2_fd)
        fcntl.flock(ask_fd, fcntl.LOCK_UN)
        os.close(ask_fd)


def test_concurrent_fresh_acquire_distinct_suffixes(
    isolated_lane_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ask_fd = cdp_lane._open_lock(cdp_lane.lock_path_for("ask"))
    fcntl.flock(ask_fd, fcntl.LOCK_EX)
    monkeypatch.setattr(cdp_lane, "_launch_chrome", lambda port, profile: 1)
    monkeypatch.setattr(cdp_lane, "is_listening", lambda port: False)
    monkeypatch.setattr(cdp_lane, "chrome_port_for_profile", lambda profile: None)

    start = threading.Barrier(2)
    hold = threading.Barrier(2)
    results: list[str] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            start.wait(timeout=5)
            with cdp_lane.acquire_lane(
                "ask", fresh=True, queue_timeout_s=0, launch=True
            ) as info:
                results.append(info.suffix)
                hold.wait(timeout=5)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    fcntl.flock(ask_fd, fcntl.LOCK_UN)
    os.close(ask_fd)

    assert not errors, errors
    assert sorted(results) == ["ask-2", "ask-3"]


def test_lane_busy_error_names_fresh_profile_escape() -> None:
    msg = "for a parallel lane use --fresh-profile"
    with pytest.raises(cdp_lane.LaneBusyError, match=msg):
        raise cdp_lane.LaneBusyError(
            "profile 'ask' is actively leased (queue timeout 0s); "
            "for a parallel lane use --fresh-profile"
        )
