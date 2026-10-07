"""HTTP routes for the jobs satellite. Exit codes are body fields, not statuses.

Create accepts only ``args``, ``output_contract``, and ``target_thread``.
DELETE is cancel. GET status is 200 for every known run, including a
non-zero exit. The relay rewrites the origin ``href`` and ``Location`` values
this module emits.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from typing import Any, NoReturn
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import ValidationError

from jobs import events
from jobs.auth import require_token
from jobs.errors import JobsError
from jobs.journal import Fold
from jobs.spec import JobSpec

router = APIRouter(prefix="/api/v1/jobs", dependencies=[Depends(require_token)])

_CREATE_KEYS = frozenset({"args", "output_contract", "target_thread"})
_THREAD = re.compile(r"^[0-9]{1,12}$")


def _today(request: Request) -> date:
    clock = getattr(request.app.state, "jobs_today", None)
    if clock is not None:
        return clock()
    return datetime.now(UTC).date()


def _surface(request: Request) -> str:
    raw = request.headers.get("x-ulg-surface")
    if raw is None:
        raise JobsError("surface_undeclared", "X-ULG-Surface is required", 403)
    if raw not in {"life", "code"}:
        raise JobsError("surface_invalid", "X-ULG-Surface must be life or code", 422)
    return raw


def _spec(request: Request, job: str) -> JobSpec:
    spec = request.app.state.jobs_specs.get(job)
    if spec is None:
        raise JobsError("job_not_found", f"Unknown job {job}", 404, data={"job": job})
    return spec


def _member(spec: JobSpec, surface: str, job: str) -> None:
    if surface not in spec.surfaces:
        raise JobsError(
            "surface_not_member",
            f"{surface} is not a member of {job}",
            403,
            data={"job": job, "surface": surface},
        )


def _reject(job: str, code: str, surface: str, exc: JobsError) -> NoReturn:
    events.emit_rejected(job=job, code=code, surface=surface)
    raise exc


def _href(job: str, run_id: str | None = None) -> str:
    base = f"/api/v1/jobs/{job}"
    if run_id is None:
        return base
    return f"{base}/runs/{run_id}"


def _representation(request: Request, fold: Fold) -> dict[str, Any]:
    delivery = request.app.state.jobs_journal.latest_delivery(fold.run_id)
    delivery_body: dict[str, Any] | None = None
    if delivery is not None:
        delivery_body = {"state": delivery[0], **delivery[1]}
    error = fold.data.get("error")
    return {
        "run_id": fold.run_id,
        "job": fold.job,
        "status": fold.state,
        "exit_code": fold.data.get("exit_code"),
        "result": fold.data.get("result"),
        "error": error,
        "created_at": fold.created_at,
        "updated_at": fold.updated_at,
        "progress_seq": request.app.state.jobs_runner.progress_seq(fold.run_id),
        "source": "jobs.journal",
        "as_of": fold.updated_at,
        "recovery": fold.data.get("recovery"),
        "output_contract": fold.output_contract,
        "target_thread": fold.target_thread,
        "delivery": delivery_body,
        "pgid": fold.data.get("pgid"),
        "links": {
            "self": {"href": _href(fold.job, fold.run_id)},
            "log": {"href": f"{_href(fold.job, fold.run_id)}/log"},
            "capability": {"href": _href(fold.job)},
        },
    }


@router.get("", operation_id="jobs_catalog")
async def catalog(request: Request) -> JSONResponse:
    """List jobs whose surfaces include the caller's X-ULG-Surface value."""
    surface = _surface(request)
    today = _today(request)
    members = []
    for spec in request.app.state.jobs_specs.values():
        if surface not in spec.surfaces:
            continue
        members.append(
            {
                "name": spec.name,
                "description": spec.description,
                "href": _href(spec.name),
                "sunset": spec.sunset.isoformat(),
                "sunset_passed": today > spec.sunset,
                "handle": spec.handle,
            }
        )
    response = JSONResponse({"members": members})
    response.headers["Link"] = '</api/v1/jobs/openapi.json>; rel="service-desc"'
    return response


@router.get("/{job}", operation_id="jobs_spec")
async def job_spec(job: str, request: Request) -> dict[str, Any]:
    """Return one job's argument schema, sunset, and graduation target."""
    surface = _surface(request)
    spec = _spec(request, job)
    _member(spec, surface, job)
    result_schema = None
    if spec.result_schema is not None:
        result_schema = spec.result_schema.model_json_schema()
    return {
        "name": spec.name,
        "description": spec.description,
        "surfaces": sorted(spec.surfaces),
        "sunset": spec.sunset.isoformat(),
        "sunset_passed": _today(request) > spec.sunset,
        "graduates_to": spec.graduates_to.model_dump(),
        "handle": spec.handle,
        "idle_seconds": spec.idle_seconds,
        "args_schema": spec.args_model.model_json_schema(),
        "result_schema": result_schema,
    }


@router.post("/{job}", operation_id="jobs_create_run", status_code=202)
async def create_run(job: str, request: Request) -> JSONResponse:
    """Admit a run. Forbidden top-level keys and bad args are 422."""
    surface = _surface(request)
    try:
        spec = _spec(request, job)
    except JobsError as exc:
        _reject(job, exc.code, surface, exc)
    _member(spec, surface, job)
    if _today(request) > spec.sunset:
        _reject(
            job,
            "job_sunset",
            surface,
            JobsError("job_sunset", f"{job} is past sunset", 410, data={"job": job}),
        )
    try:
        payload = json.loads(await request.body() or b"{}")
    except json.JSONDecodeError as exc:
        raise JobsError("body_invalid", "create body is not JSON", 422) from exc
    if not isinstance(payload, dict):
        raise JobsError("body_invalid", "create body must be an object", 422)
    extra = set(payload) - _CREATE_KEYS
    if extra:
        field = sorted(extra)[0]
        _reject(
            job,
            "body_field_forbidden",
            surface,
            JobsError(
                "body_field_forbidden",
                f"field {field} is not accepted",
                422,
                data={"field": field},
            ),
        )
    contract = payload.get("output_contract", "inline")
    target = payload.get("target_thread")
    if contract not in {"inline", "thread"}:
        raise JobsError("output_contract_invalid", "output_contract is not accepted", 422)
    if contract == "thread" and not target:
        _reject(
            job,
            "target_thread_required",
            surface,
            JobsError("target_thread_required", "thread delivery needs target_thread", 422),
        )
    if contract == "inline" and target:
        _reject(
            job,
            "target_thread_requires_thread_contract",
            surface,
            JobsError(
                "target_thread_requires_thread_contract",
                "target_thread requires output_contract thread",
                422,
            ),
        )
    if target is not None and _THREAD.fullmatch(str(target)) is None:
        raise JobsError("target_thread_invalid", "target_thread is not a thread id", 422)
    try:
        args = spec.args_model.model_validate(payload.get("args") or {})
    except ValidationError as exc:
        _reject(
            job,
            "args_invalid",
            surface,
            JobsError(
                "args_invalid",
                "args did not match the job schema",
                422,
                data={"errors": _jsonable_errors(exc)},
            ),
        )
    run_id = uuid4().hex
    request.app.state.jobs_journal.admit(
        run_id=run_id,
        job=job,
        args=args.model_dump(mode="json"),
        surface=surface,
        output_contract=contract,
        target_thread=str(target) if target is not None else None,
    )
    request.app.state.jobs_runner.schedule(run_id)
    monitor = _href(job, run_id)
    body = {
        "run_id": run_id,
        "job": job,
        "status": "admitted",
        "links": {
            "monitor": {"href": monitor},
            "self": {"href": monitor},
            "log": {"href": f"{monitor}/log"},
            "capability": {"href": _href(job)},
        },
    }
    response = JSONResponse(body, status_code=202)
    response.headers["Location"] = monitor
    return response


@router.get("/{job}/runs/{run_id}", operation_id="jobs_run_status")
async def run_status(
    job: str,
    run_id: str,
    request: Request,
    wait: int = Query(default=0),
) -> dict[str, Any]:
    """Return the journal fold. ``wait`` blocks up to 60s for a change."""
    surface = _surface(request)
    spec = _spec(request, job)
    _member(spec, surface, job)
    if wait < 0 or wait > 60:
        raise JobsError("wait_out_of_range", "wait must be between 0 and 60", 422)
    fold = _known(request, job, run_id)
    if wait:
        await request.app.state.jobs_runner.wait_for_change(
            run_id, wait, fold.state, request.app.state.jobs_runner.progress_seq(run_id)
        )
        fold = _known(request, job, run_id)
    return _representation(request, fold)


@router.get("/{job}/runs/{run_id}/log", operation_id="jobs_run_log")
async def run_log(
    job: str,
    run_id: str,
    request: Request,
    offset: int = Query(default=0),
) -> Response:
    """Return log bytes from ``offset`` and set ``X-Log-Next-Offset``."""
    surface = _surface(request)
    spec = _spec(request, job)
    _member(spec, surface, job)
    _known(request, job, run_id)
    path = request.app.state.jobs_journal.log_path(run_id)
    size = path.stat().st_size if path.is_file() else 0
    if offset < 0 or offset > size:
        raise JobsError(
            "offset_out_of_range",
            "log offset is outside the file",
            416,
            data={"size": size},
        )
    data = b""
    if path.is_file() and offset < size:
        with path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read()
    response = PlainTextResponse(data.decode("utf-8", errors="replace"))
    response.headers["X-Log-Next-Offset"] = str(offset + len(data))
    return response


@router.delete("/{job}/runs/{run_id}", operation_id="jobs_run_cancel")
async def cancel_run(job: str, run_id: str, request: Request) -> JSONResponse:
    """Cancel a run. Admitted appends cancelled and does not signal a group."""
    surface = _surface(request)
    spec = _spec(request, job)
    _member(spec, surface, job)
    _known(request, job, run_id)
    status, name = await request.app.state.jobs_runner.cancel(run_id)
    if status == 409:
        raise JobsError("run_terminal", "run is already terminal", 409)
    if status == 404:
        raise JobsError("run_not_found", "run not found", 404)
    fold = request.app.state.jobs_journal.fold(run_id)
    body = {"run_id": run_id, "status": fold.state if fold else name}
    return JSONResponse(body, status_code=status)


def _jsonable_errors(exc: ValidationError) -> list[dict[str, Any]]:
    """Drop exception objects from pydantic error context so the envelope encodes."""
    cleaned: list[dict[str, Any]] = []
    for item in exc.errors():
        row = dict(item)
        ctx = row.get("ctx")
        if isinstance(ctx, dict):
            row["ctx"] = {str(key): str(value) for key, value in ctx.items()}
        cleaned.append(row)
    return cleaned


def _known(request: Request, job: str, run_id: str) -> Fold:
    fold = request.app.state.jobs_journal.fold(run_id)
    if fold is None or fold.job != job:
        raise JobsError("run_not_found", "run not found", 404)
    return fold
