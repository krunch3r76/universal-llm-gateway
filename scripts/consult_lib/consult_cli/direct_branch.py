"""Direct consultation branch orchestration and shared failure handling.

The direct path covers parallel, chained, and role-backed pipeline invocations
outside reviewer mode while preserving artifact and event semantics.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from consult_lib.core import execute_consult
from consult_lib.grounding_guard import (
    report_grounding_results,
    validate_model_grounding,
    write_grounding_artifact,
)
from consult_lib.history import (
    extract_pipeline_step_records,
    resolve_pipeline_models,
    write_consult_call_event,
)
from consult_lib.model_selection import SelectionFailure
from consult_lib.output import format_output
from consult_lib.run_artifact import (
    STATUS_PIPELINE_FAILED,
    STATUS_SELECTION_FAILED,
    STATUS_SUCCESS,
    RunArtifact,
)

from .grounding import (
    _observe_grounding_outcomes,
    _remap_grounding_results,
    _results_to_dicts,
)
from .reviewer_pipeline import (
    _fail_with_artifact,
    _finalize_output,
    _resolve_used_models_for_pipeline,
    _write_started_event,
)


def _run_direct_branch(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    role_requirements: dict[str, dict[str, object]],
    started_at: float,
    call_id: str,
    role_pipeline_id: str | None,
) -> None:
    """Execute non-reviewer consultation branch with full artifact capture."""
    mode = "pipeline" if role_pipeline_id else ("chained" if args.chain else "parallel")
    preview_models = args.models or []
    if role_pipeline_id and not args.models:
        effective_requirements = role_requirements
        if args.cloud_only and args.role in role_requirements:
            effective_requirements = {
                **role_requirements,
                args.role: {**role_requirements[args.role], "source": "cloud"},
            }
        try:
            from consult_lib.model_selection import select_models_for_role

            preview_models, _ = select_models_for_role(
                args.role,
                effective_requirements,
                args.stargate_url,
                count=1,
            )
        except SelectionFailure:
            # Advisory only: pipeline execution remains the source of truth.
            preview_models = []
    artifact = RunArtifact(
        call_id=call_id,
        role=args.role,
        mode=mode,
        question=args.question,
        pipeline_id=role_pipeline_id,
        pipeline_virtual_model=role_pipeline_id,
        selected_models=preview_models,
    )
    _write_started_event(
        args=args,
        mode=mode,
        preview_models=preview_models,
        role_pipeline_id=role_pipeline_id,
        call_id=call_id,
        artifact=artifact,
    )
    artifact.checkpoint()
    started_wall = time.time()
    try:
        results = execute_consult(
            args.question,
            role=args.role,
            context_files=[Path(p) for p in args.context_files] if args.context_files else [],
            scope=args.scope,
            chain=args.chain,
            models=args.models,
            stargate_url=args.stargate_url,
            rag_url=args.rag_url,
            no_rag=args.no_rag,
            use_rag_pipeline=args.rag_pipeline is not None and not args.no_rag_pipeline,
            rag_top_k=args.rag_top_k,
            timeout=args.timeout,
            chain_directive=args.chain_directive,
            cloud_only=args.cloud_only,
            pipeline_options=getattr(args, "pipeline_options_parsed", None),
        )
    except (ValueError, RuntimeError) as exc:
        _fail_direct(
            parser=parser,
            artifact=artifact,
            exc=exc,
            args=args,
            mode=mode,
            role_pipeline_id=role_pipeline_id,
            started_at=started_at,
            started_wall=started_wall,
            call_id=call_id,
        )
        return
    except Exception as exc:
        _fail_direct(
            parser=parser,
            artifact=artifact,
            exc=exc,
            args=args,
            mode=mode,
            role_pipeline_id=role_pipeline_id,
            started_at=started_at,
            started_wall=started_wall,
            call_id=call_id,
            generic=True,
        )
        return

    # Capture chain intermediate outputs as partials for later agents.
    result_dicts = _results_to_dicts(results)
    if args.chain:
        for r_dict in result_dicts:
            artifact.record_chain_phase(r_dict)

    autoselect_path = next(
        (r.selection_path for r in results if r.selection_path), None
    )
    exec_id = next((r.execution_id for r in results if r.execution_id), None)
    # Recover recorder-backed step outputs for pipeline-backed roles.
    result_pipeline_id = (
        next((r.pipeline_id for r in results if r.pipeline_id), None)
        or role_pipeline_id
    )
    if result_pipeline_id and exec_id:
        for step_record in extract_pipeline_step_records(
            execution_id=exec_id,
            pipeline_id=result_pipeline_id,
        ):
            artifact.record_partial_step(step_record)

    selected_models, used_models = _resolve_used_models_for_pipeline(
        role_pipeline_id=role_pipeline_id,
        results=results,
        args_models=args.models,
        execution_id=exec_id,
    )
    artifact.selected_models = selected_models

    output = format_output(
        args.question,
        args.role,
        result_dicts,
        None,
        args.context_files,
        chained=args.chain,
        selected_models=selected_models,
        selection_path=autoselect_path,
        pipeline_virtual_model=role_pipeline_id,
        call_id=call_id,
        run_dir=str(artifact.run_dir),
    )
    print(output)

    req_config = role_requirements.get(args.role, {})
    if req_config.get("validate_grounding"):
        task = req_config.get("task", args.role)
        grounding_results = _remap_grounding_results(
            results, selected_models, role_pipeline_id
        )
        grounding_outcomes = validate_model_grounding(
            grounding_results,
            task=task,
            artifact=artifact,
        )
        if grounding_outcomes:
            report_grounding_results(grounding_outcomes)
            write_grounding_artifact(grounding_outcomes, run_dir=artifact.run_dir)
            _observe_grounding_outcomes(
                grounding_outcomes,
                task,
                args.stargate_url,
            )

    has_any_success = any(not r.error for r in results)
    status = STATUS_SUCCESS if has_any_success else STATUS_PIPELINE_FAILED
    _finalize_output(
        output=output,
        args=args,
        artifact=artifact,
        status=status,
        used_models=used_models,
        execution_id=exec_id,
        duration_seconds=time.monotonic() - started_at,
    )
    write_consult_call_event(
        role=args.role,
        mode=mode,
        question=args.question,
        selected_models=selected_models,
        used_models=used_models,
        selection_path=autoselect_path,
        pipeline_id=role_pipeline_id,
        context_files=args.context_files or [],
        output_file=args.output,
        cloud_only=args.cloud_only,
        success=has_any_success,
        error="; ".join(r.error for r in results if r.error) or None,
        duration_seconds=time.monotonic() - started_at,
        call_id=call_id,
        execution_id=exec_id,
        status=status,
        artifact_dir=str(artifact.run_dir),
        partial_output_available=bool(artifact.partial_outputs),
        chain_phase_count=len(artifact.chain_trace) if artifact.chain_trace else None,
    )


def _fail_direct(
    *,
    parser: argparse.ArgumentParser,
    artifact: RunArtifact,
    exc: Exception,
    args: argparse.Namespace,
    mode: str,
    role_pipeline_id: str | None,
    started_at: float,
    started_wall: float,
    call_id: str,
    generic: bool = False,
) -> None:
    """Shared failure handler for direct-branch exceptions and error markers."""
    error_str = f"{type(exc).__name__}: {exc}" if generic else str(exc)
    failure_kind: str | None = (
        exc.failure_kind if isinstance(exc, SelectionFailure) else None
    )
    failed_models = args.models or []
    if role_pipeline_id and not failed_models:
        exc_eid = getattr(exc, "execution_id", None)
        failed_models = resolve_pipeline_models(
            execution_id=exc_eid,
            started_at=started_wall if not exc_eid else None,
            finished_at=time.time() if not exc_eid else None,
        )
    is_selection_error = isinstance(exc, SelectionFailure) or (
        isinstance(exc, ValueError) and "No models selected" in str(exc)
    )
    status = STATUS_SELECTION_FAILED if is_selection_error else STATUS_PIPELINE_FAILED
    _report_and_fail(
        role=args.role,
        mode=mode,
        question=args.question,
        selected_models=failed_models,
        pipeline_id=role_pipeline_id,
        args=args,
        status=status,
        error=error_str,
        call_id=call_id,
        artifact=artifact,
        parser=parser,
        duration_seconds=time.monotonic() - started_at,
        failure_kind=failure_kind,
    )


def _report_and_fail(
    *,
    role: str,
    mode: str,
    question: str,
    selected_models: list[str],
    pipeline_id: str | None,
    args: argparse.Namespace,
    status: str,
    error: str,
    call_id: str,
    artifact: RunArtifact,
    parser: argparse.ArgumentParser,
    duration_seconds: float,
    failure_kind: str | None,
) -> None:
    """Emit failure event and finalize failure artifact in one shared path."""
    write_consult_call_event(
        role=role,
        mode=mode,
        question=question,
        selected_models=selected_models,
        used_models=[],
        selection_path=None,
        pipeline_id=pipeline_id,
        context_files=args.context_files or [],
        output_file=args.output,
        cloud_only=args.cloud_only,
        success=False,
        error=error,
        duration_seconds=duration_seconds,
        call_id=call_id,
        status=status,
        artifact_dir=str(artifact.run_dir),
        failure_kind=failure_kind,
    )
    _fail_with_artifact(
        parser=parser,
        artifact=artifact,
        status=status,
        error=error,
        args=args,
        duration_seconds=duration_seconds,
    )
