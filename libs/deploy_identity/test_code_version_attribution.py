"""Regression: code_version must not report a checkout HEAD it cannot attribute.

The defect this pins: ``resolve_code_version`` resolved lazily on first call, so
a long-running service probed for the first time minutes after start reported
whatever the shared checkout HEAD had since become. Two cortex-api processes
started in the same second reported different SHAs on 2026-07-31 purely because
their first ``/health`` probes landed either side of a sibling's commit.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from deploy_identity import code_version as cv


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    cv.reset_code_version_cache_for_tests()
    monkeypatch.delenv("ULG_CODE_VERSION", raising=False)
    monkeypatch.setattr(cv, "_stamp_path", lambda: cv.Path("/nonexistent/stamp"))
    yield
    cv.reset_code_version_cache_for_tests()


def _patched_git(sha: str):
    return patch.multiple(
        cv,
        subprocess=type(
            "S",
            (),
            {"run": staticmethod(lambda *a, **k: type("R", (), {"stdout": sha})())},
        ),
        get_workspace_root=lambda: "/repo",
    )


def test_stale_process_withholds_checkout_head(monkeypatch):
    """A first resolution long after exec must not report checkout HEAD."""
    monkeypatch.setattr(cv, "process_age_s", lambda: 3600.0)
    with _patched_git("cd619ca9" * 5):
        assert cv.resolve_code_version() == "unknown"


def test_young_process_reports_checkout_head(monkeypatch):
    """Within the attribution window the checkout HEAD is the loaded code."""
    monkeypatch.setattr(cv, "process_age_s", lambda: 0.4)
    with _patched_git("cd619ca9" * 5):
        assert cv.resolve_code_version() == "cd619ca9" * 5


def test_stamp_still_wins_when_process_is_old(monkeypatch, tmp_path):
    """A source-sync stamp is an observation, so age does not withhold it."""
    stamp = tmp_path / ".source_sync_stamp"
    stamp.write_text(
        "2026-07-31T20:34:10Z\nfeedfacefeedfacefeedfacefeedfacefeedface\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cv, "_stamp_path", lambda: stamp)
    monkeypatch.setattr(cv, "process_age_s", lambda: 99999.0)
    assert cv.resolve_code_version() == "feedfacefeedfacefeedfacefeedfacefeedface"


def test_env_override_still_wins_when_process_is_old(monkeypatch):
    """ULG_CODE_VERSION is fixed at exec, so it stays attributable forever."""
    override = "b" * 40
    monkeypatch.setenv("ULG_CODE_VERSION", override)
    monkeypatch.setattr(cv, "process_age_s", lambda: 99999.0)
    assert cv.resolve_code_version() == override


def test_two_probes_of_one_process_cannot_diverge(monkeypatch):
    """The divergence that produced the defect: HEAD moves between probes."""
    monkeypatch.setattr(cv, "process_age_s", lambda: 0.4)
    with _patched_git("82f07260" * 5):
        first = cv.resolve_code_version()
    with _patched_git("dba38ed7" * 5):
        second = cv.resolve_code_version()
    assert first == second == "82f07260" * 5


def test_process_age_is_observed_not_asserted():
    """process_age_s reads /proc; a live call must be a plausible positive."""
    age = cv.process_age_s()
    assert age is None or age >= 0.0
