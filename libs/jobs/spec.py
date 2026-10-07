"""JobSpec model, load-time lint, and the bounded registry loader.

The jobs server and the production registry call ``load_registry``. A spec
names an executable by repo-relative path; request bodies never supply a
path, a shell string, or an argv list. More than ``MAX_JOBS`` specs, a
duplicate name, or a path-shaped argument field fails closed at load.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_JOBS = 4
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
_FORBIDDEN_FIELD = re.compile(
    r"(?i)^(path|.*_path|file|.*_file|dir|.*_dir|.*dir|cmd|command|"
    r"argv|shell|script|exec|executable)$"
)


class JobSpecInvalidError(ValueError):
    """A JobSpec failed load-time lint, naming, or executable checks."""


class RegistryUnboundedError(ValueError):
    """The registry has more than MAX_JOBS specs or a duplicate name."""


class GraduationTarget(BaseModel):
    """Typed owner a JobSpec must name before it may stay in the registry.

    ``satellite_route`` owners are manage service slugs or ``web_fetcher``.
    ``manage_lifecycle`` owners are Stage 5 daemon family names. ``note`` is
    one sentence the catalog can show. The sunset date lives on the JobSpec.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["satellite_route", "manage_lifecycle"]
    owner: str
    note: str = Field(max_length=200)


class JobSpec(BaseModel):
    """One bounded host job: argv template, surfaces, graduation, and sunset.

    The runner calls ``argv`` with a validated args model and executes the
    resolved ``executable``. ``pre_run`` may page or refuse before spawn.
    ``result_schema`` parses the final stdout JSON line when set. Frozen so
    a request cannot mutate the registry row.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    name: str
    description: str
    surfaces: frozenset[Literal["life", "code"]]
    args_model: type[BaseModel]
    argv: Callable[[BaseModel], list[str]]
    executable: str
    result_schema: type[BaseModel] | None = None
    idle_seconds: int
    graduates_to: GraduationTarget
    sunset: date
    handle: str
    pre_run: Callable[..., Any] | None = None


def repo_root() -> Path:
    """Return the universal-llm-gateway checkout that contains ``libs/jobs``."""
    return Path(__file__).resolve().parents[2]


def lint_args_model(model: type[BaseModel]) -> None:
    """Raise JobSpecInvalidError when an args field names a path, dir, or command.

    The pattern includes ``.*dir$`` so a subdirectory field cannot smuggle a
    filesystem location through the create body. ``Path`` annotations fail
    the same way, including inside unions.
    """
    for name, field in model.model_fields.items():
        if _FORBIDDEN_FIELD.match(name):
            raise JobSpecInvalidError(f"forbidden args field {name}")
        if _annotation_is_path(field.annotation):
            raise JobSpecInvalidError(f"path type on args field {name}")


def _annotation_is_path(annotation: Any) -> bool:
    if annotation is Path:
        return True
    origin = getattr(annotation, "__origin__", None)
    args = getattr(annotation, "__args__", ())
    if origin is None:
        return False
    return any(_annotation_is_path(arg) for arg in args)


def load_registry(
    specs: Sequence[JobSpec] | None = None,
) -> tuple[JobSpec, ...]:
    """Validate and return the job registry, production specs when omitted.

    Checks the name pattern, the hard cap, duplicate names, argument lint,
    a non-empty surface set, and that each executable exists under the repo
    root. Does not apply the sunset; create-run returns 410 after that date.
    """
    if specs is None:
        from jobs.registry import production_specs

        specs = production_specs()
    if len(specs) > MAX_JOBS:
        raise RegistryUnboundedError(f"registry has {len(specs)} specs; cap is {MAX_JOBS}")
    seen: set[str] = set()
    root = repo_root()
    for spec in specs:
        if spec.name in seen:
            raise RegistryUnboundedError(f"duplicate job name {spec.name}")
        seen.add(spec.name)
        if NAME_RE.fullmatch(spec.name) is None:
            raise JobSpecInvalidError(f"job name {spec.name!r} is not a member slug")
        if not spec.surfaces:
            raise JobSpecInvalidError(f"{spec.name} has no surfaces")
        lint_args_model(spec.args_model)
        path = (root / spec.executable).resolve()
        if not path.is_file():
            raise JobSpecInvalidError(f"{spec.name} executable missing: {spec.executable}")
        if len(spec.graduates_to.note) > 200:
            raise JobSpecInvalidError(f"{spec.name} graduation note exceeds 200 chars")
    return tuple(specs)


def resolved_executable(spec: JobSpec) -> Path:
    """Resolve a spec's repo-relative executable against the checkout root."""
    return (repo_root() / spec.executable).resolve()
