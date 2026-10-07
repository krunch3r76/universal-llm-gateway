"""The jobs package does not import the pipeline control plane."""

from __future__ import annotations

from pathlib import Path

import pytest

_BANNED = (
    "systems.pipeline",
    "dispatch_journal",
    "async_tracker_delivery",
    "/api/v1/executions",
)


@pytest.mark.offline
def test_jobs_package_has_no_control_plane_imports() -> None:
    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for needle in _BANNED:
            if needle in text:
                offenders.append(f"{path.name}: {needle}")
    assert offenders == []
