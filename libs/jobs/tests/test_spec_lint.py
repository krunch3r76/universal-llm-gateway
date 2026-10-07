"""Load-time rejection of path-shaped argument fields."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict

from jobs.spec import GraduationTarget, JobSpec, JobSpecInvalidError, load_registry


def _spec(model: type[BaseModel]) -> JobSpec:
    return JobSpec(
        name="lint-job",
        description="Spec used only to exercise argument-field lint.",
        surfaces=frozenset({"code"}),
        args_model=model,
        argv=lambda _args: [],
        executable="scripts/ingest-article",
        idle_seconds=30,
        graduates_to=GraduationTarget(
            kind="manage_lifecycle",
            owner="watch",
            note="Lint fixture is not a production job.",
        ),
        sunset=date(2027, 4, 1),
        handle="capability:jobs/lint-job",
    )


@pytest.mark.offline
def test_path_named_field_rejected_at_load() -> None:
    class PathArgs(BaseModel):
        model_config = ConfigDict(extra="forbid")
        path: str

    with pytest.raises(JobSpecInvalidError):
        load_registry((_spec(PathArgs),))


@pytest.mark.offline
def test_dir_suffix_rejected_at_load() -> None:
    class DirArgs(BaseModel):
        model_config = ConfigDict(extra="forbid")
        subdir: str

    with pytest.raises(JobSpecInvalidError):
        load_registry((_spec(DirArgs),))


@pytest.mark.offline
def test_path_type_rejected_at_load() -> None:
    class Typed(BaseModel):
        model_config = ConfigDict(extra="forbid")
        location: Path

    with pytest.raises(JobSpecInvalidError):
        load_registry((_spec(Typed),))
