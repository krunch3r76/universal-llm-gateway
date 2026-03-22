"""Reviewer pipeline branch orchestration and artifact lifecycle utilities.

This module owns reviewer-specific execution flow and shared failure/output
helpers used by direct mode to keep branching code modular and testable.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from consult_lib.context import collect_context_file_paths
from consult_lib.core import ConsultResult
from consult_lib.grounding_guard import (
    report_grounding_results,
    validate_model_grounding,
    write_grounding_artifact,
)
from consult_lib.history import (
    resolve_pipeline_models,
    write_consult_call_event,
    write_consult_call_started_event,
)
from consult_lib.model_selection import SelectionFailure, select_models_for_role
from consult_lib.output import format_pipeline_review_output
from consult_lib.pipeline import (
    build_reviewer_pipeline_model_overrides,
    run_code_review_pipeline,
)
from consult_lib.run_artifact import (
    STATUS_PARTIAL_OUTPUT_AVAILABLE,
    STATUS_PIPELINE_FAILED,
    STATUS_SELECTION_FAILED,
    STATUS_SUCCESS,
    RunArtifact,
    validate_file_lines,
    write_failure_marker,
    write_output_safe,
)

from .grounding import _observe_grounding_outcomes


def _log_pipeline_message(message: str, artifact: RunArtifact) -> None:
    """Emit a pipeline progress message to stderr and artifact stderr log."""
    print(message, file=sys.stderr)
    artifact.record_stderr(message)


def _write_started_event(
    *,
    args: argparse.Namespace,
    mode: str,
    preview_models: list[str],
    role_pipeline_id: str | None,
    call_id: str,
    artifact: RunArtifact,
) -> None:
    """Write consult_call.started with shared fields across execution modes."""
    write_consult_call_started_event(
        role=args.role,
        mode=mode,
        question=args.question,
        selected_models=preview_models,
        pipeline_id=role_pipeline_id,
        context_files=args.context_files or [],
        cloud_only=bool(args.cloud_only),
        call_id=call_id,
        artifact_dir=str(artifact.run_dir),
    )


def _run_reviewer_pipeline(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    target_models: list[str],
    artifact: RunArtifact,
) -> tuple[str, list[dict[str, Any]]]:
    """Execute reviewer pipeline batches and build formatted report output.

    Args:
        args: Parsed CLI arguments for the current consult invocation.
        parser: Shared parser used for fatal usage/flow errors.
        target_models: Reviewer model IDs selected for this run.
        artifact: Run artifact collector for partial outputs and stderr logs.

    Returns:
        A tuple of formatted report text and raw batch result dictionaries.
    """
    context_file_paths = collect_context_file_paths(args.context_files)
    if not context_file_paths:
        parser.error("--pipeline requires at least one --context-files path")
    pipeline_options: dict[str, Any] | None = None
    overrides = build_reviewer_pipeline_model_overrides(target_models)
    if overrides:
        pipeline_options = {"model_ref_overrides": overrides}
        msg = "Pipeline reviewer model overrides: " + ", ".join(
            f"{k}={v}" for k, v in overrides.items()
        )
        _log_pipeline_message(msg, artifact)
    msg2 = f"Pipeline mode: estimating batches for {len(context_file_paths)} files"
    _log_pipeline_message(msg2, artifact)
    artifact.checkpoint()
    try:
        batch_results = run_code_review_pipeline(
            stargate_url=args.stargate_url,
            files=context_file_paths,
            timeout=args.timeout,
            pipeline_options=pipeline_options,
        )
    except RuntimeError as exc:
        parser.error(f"--pipeline failed: {exc}")
    except Exception as exc:
        parser.error(f"--pipeline failed ({type(exc).__name__}): {exc}")
    for batch in batch_results:
        artifact.record_partial(
            step="batch",
            model_id=batch.get("model_id", "code-review"),
            content=str(batch.get("result", batch.get("error", ""))),
            execution_id=batch.get("execution_id"),
        )
    output = format_pipeline_review_output(
        question=args.question,
        file_paths=context_file_paths,
        batches=batch_results,
        selected_models=target_models,
        pipeline_virtual_model="code-review",
        call_id=artifact.call_id,
        run_dir=str(artifact.run_dir),
    )
    return output, batch_results


def _resolve_used_models_for_pipeline(
    *,
    role_pipeline_id: str | None,
    results: list[ConsultResult],
    args_models: list[str] | None,
    execution_id: str | None,
) -> tuple[list[str], list[str]]:
    """Derive selected and used model lists for pipeline-backed and direct roles.

    Args:
        role_pipeline_id: Optional pipeline virtual model ID for the role.
        results: Consult results produced by direct or pipeline execution.
        args_models: Explicitly requested models from CLI, if present.
        execution_id: Pipeline execution ID for exact model resolution.

    Returns:
        A pair of lists containing selected models and successfully used models.
    """
    selected_models = args_models[:] if args_models else []
    if not selected_models:
        if role_pipeline_id:
            selected_models = resolve_pipeline_models(execution_id=execution_id)
        if not selected_models:
            selected_models = [str(r.model_id) for r in results if r.model_id]
    used_models = [str(r.model_id) for r in results if not r.error and r.model_id]
    if role_pipeline_id and not used_models:
        used_models = selected_models if all(not r.error for r in results) else []
    return selected_models, used_models


def _finalize_output(
    *,
    output: str,
    args: argparse.Namespace,
    artifact: RunArtifact,
    status: str,
    used_models: list[str],
    execution_id: str | None,
    duration_seconds: float,
) -> None:
    """Persist artifact output, optional output file, and FILE: validation report.

    Args:
        output: Final response text emitted to stdout.
        args: Parsed CLI arguments controlling output and validation behavior.
        artifact: Run artifact writer instance for metadata and partial traces.
        status: Final run status code persisted in metadata.
        used_models: Concrete model IDs used during execution.
        execution_id: Optional pipeline execution ID for correlation.
        duration_seconds: End-to-end execution duration for this run.
    """
    run_dir = artifact.finalize(
        status=status,
        output_text=output,
        used_models=used_models,
        execution_id=execution_id,
        duration_seconds=duration_seconds,
    )
    if args.output:
        out_path = Path(args.output)
        write_output_safe(path=out_path, content=output, artifact=artifact)
        # Re-flush metadata so output_path is persisted.
        artifact.checkpoint()
        print(f"Saved: {out_path}", file=sys.stderr)
    print(f"Run artifacts: {run_dir}/metadata.json", file=sys.stderr)
    if args.validate_files:
        import json as _json

        result = validate_file_lines(output)
        if result["valid"]:
            print(
                "FILE validation — valid paths: " + ", ".join(result["normalized"]),
                file=sys.stderr,
            )
        if result["invalid"]:
            print(
                "FILE validation — INVALID paths (hallucinated or moved): "
                + ", ".join(result["invalid"]),
                file=sys.stderr,
            )
        (run_dir / "file_validation.json").write_text(
            _json.dumps(result, indent=2), encoding="utf-8"
        )


def _fail_with_artifact(
    *,
    parser: argparse.ArgumentParser,
    artifact: RunArtifact,
    status: str,
    error: str,
    args: argparse.Namespace,
    duration_seconds: float,
    raise_sys_exit: bool = True,
) -> None:
    """Finalize failure artifacts, overwrite output marker, and optionally abort.

    Args:
        parser: Parser used to raise usage-style fatal errors.
        artifact: Run artifact writer for failed metadata snapshots.
        status: Failure status value persisted in artifact metadata.
        error: Human-readable error summary shown to users.
        args: Parsed CLI args containing optional output-file destination.
        duration_seconds: Runtime elapsed before failure.
        raise_sys_exit: If True, call parser.error to terminate invocation.
    """
    artifact.finalize(
        status=status,
        error_summary=error,
        duration_seconds=duration_seconds,
    )
    if args.output:
        write_failure_marker(
            path=Path(args.output),
            call_id=artifact.call_id,
            status=status,
            error_summary=error,
            run_dir=str(artifact.run_dir),
        )
    if raise_sys_exit:
        parser.error(error)


def _handle_selection_failure(
    *,
    exc: Exception,
    failure_kind: str | None,
    args: argparse.Namespace,
    started_at: float,
    call_id: str,
    role_pipeline_id: str | None,
    reviewer_selection_path: str | None,
    selected_models: list[str],
    artifact: RunArtifact,
    parser: argparse.ArgumentParser,
) -> None:
    """Emit consistent selection-failure events and finalize failure artifact."""
    error_text = str(exc)
    write_consult_call_event(
        role=args.role,
        mode="pipeline",
        question=args.question,
        selected_models=selected_models,
        used_models=[],
        selection_path=reviewer_selection_path,
        pipeline_id=role_pipeline_id,
        context_files=args.context_files or [],
        output_file=args.output,
        cloud_only=args.cloud_only,
        success=False,
        error=error_text,
        duration_seconds=time.monotonic() - started_at,
        call_id=call_id,
        status=STATUS_SELECTION_FAILED,
        artifact_dir=str(artifact.run_dir),
        failure_kind=failure_kind,
    )
    _fail_with_artifact(
        parser=parser,
        artifact=artifact,
        status=STATUS_SELECTION_FAILED,
        error=error_text,
        args=args,
        duration_seconds=time.monotonic() - started_at,
    )


def _run_pipeline_branch(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    role_requirements: dict[str, Any],
    started_at: float,
    call_id: str,
    role_pipeline_id: str | None,
) -> None:
    """Execute reviewer pipeline branch with full artifact capture and telemetry.

    Args:
        args: Parsed CLI arguments for the invocation.
        parser: Shared parser used for fatal flow exits.
        role_requirements: Role configuration map from consult role definitions.
        started_at: Monotonic timestamp captured at command start.
        call_id: Stable call identifier for event/metadata correlation.
        role_pipeline_id: Virtual pipeline model ID bound to the current role.
    """
    started_selected_models = args.models or []
    target_models = args.models or []
    reviewer_selection_path: str | None = None
    artifact = RunArtifact(
        call_id=call_id,
        role=args.role,
        mode="pipeline",
        question=args.question,
        pipeline_id=role_pipeline_id,
        pipeline_virtual_model="code-review",
        selected_models=started_selected_models,
    )
    reviewer_requirements = role_requirements
    if args.cloud_only and "reviewer" in role_requirements:
        reviewer_requirements = {
            **role_requirements,
            "reviewer": {**role_requirements["reviewer"], "source": "cloud"},
        }
    try:
        if not args.models:
            selected_models_list, reviewer_selection_path = select_models_for_role(
                "reviewer", reviewer_requirements, args.stargate_url
            )
            started_selected_models = selected_models_list
            target_models = selected_models_list
            artifact.selected_models = selected_models_list
    except SelectionFailure as exc:
        _handle_selection_failure(
            exc=exc,
            failure_kind=exc.failure_kind,
            args=args,
            started_at=started_at,
            call_id=call_id,
            role_pipeline_id=role_pipeline_id,
            reviewer_selection_path=reviewer_selection_path,
            selected_models=started_selected_models,
            artifact=artifact,
            parser=parser,
        )
        return
    except Exception as exc:
        _handle_selection_failure(
            exc=RuntimeError(f"Model selection failed: {exc}"),
            failure_kind=None,
            args=args,
            started_at=started_at,
            call_id=call_id,
            role_pipeline_id=role_pipeline_id,
            reviewer_selection_path=reviewer_selection_path,
            selected_models=started_selected_models,
            artifact=artifact,
            parser=parser,
        )
        return
    _write_started_event(
        args=args,
        mode="pipeline",
        preview_models=started_selected_models,
        role_pipeline_id=role_pipeline_id,
        call_id=call_id,
        artifact=artifact,
    )

    started_wall = time.time()
    try:
        output, batch_results = _run_reviewer_pipeline(
            args, parser, target_models, artifact
        )
    except SystemExit:
        fail_status = (
            STATUS_PARTIAL_OUTPUT_AVAILABLE
            if artifact.partial_outputs
            else STATUS_PIPELINE_FAILED
        )
        write_consult_call_event(
            role=args.role,
            mode="pipeline",
            question=args.question,
            selected_models=target_models,
            used_models=[],
            selection_path=reviewer_selection_path,
            pipeline_id=role_pipeline_id,
            context_files=args.context_files or [],
            output_file=args.output,
            cloud_only=args.cloud_only,
            success=False,
            error="pipeline mode failed",
            duration_seconds=time.monotonic() - started_at,
            call_id=call_id,
            status=fail_status,
            artifact_dir=str(artifact.run_dir),
            partial_output_available=bool(artifact.partial_outputs),
        )
        status = fail_status
        artifact.finalize(
            status=status,
            error_summary="pipeline mode failed",
            duration_seconds=time.monotonic() - started_at,
        )
        if args.output:
            write_failure_marker(
                path=Path(args.output),
                call_id=call_id,
                status=status,
                error_summary="pipeline mode failed",
                run_dir=str(artifact.run_dir),
            )
        raise

    req_config = reviewer_requirements.get("reviewer", {})
    if req_config.get("validate_grounding"):
        task = req_config.get("task", args.role)
        # Code-review pipeline returns batch dicts with keys 'model_id' and 'result' (response text).
        grounding_inputs: list[ConsultResult] = []
        for batch in batch_results:
            model_id = batch.get("model_id")
            result_text = batch.get("result")
            if not (isinstance(model_id, str) and isinstance(result_text, str)):
                print(
                    "WARN: Malformed batch item: missing model_id or result; skipping grounding for this batch.",
                    file=sys.stderr,
                )
                continue
            grounding_inputs.append(
                ConsultResult(
                    model_id=model_id,
                    response_text=result_text,
                )
            )
        grounding_outcomes = validate_model_grounding(
            grounding_inputs,
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

    print(output)
    finished_wall = time.time()
    batch_exec_ids = [
        str(eid)
        for result in batch_results
        for eid in [result.get("execution_id")]
        if eid
    ]
    actual_models: list[str] = []
    for eid in batch_exec_ids:
        for model in resolve_pipeline_models(execution_id=eid):
            if model not in actual_models:
                actual_models.append(model)
    if not actual_models:
        actual_models = resolve_pipeline_models(
            started_at=started_wall,
            finished_at=finished_wall,
        )
    final_used_models = actual_models if actual_models else []
    final_selected_models = actual_models if actual_models else target_models
    _finalize_output(
        output=output,
        args=args,
        artifact=artifact,
        status=STATUS_SUCCESS,
        used_models=final_used_models,
        execution_id=batch_exec_ids[0] if batch_exec_ids else None,
        duration_seconds=time.monotonic() - started_at,
    )
    write_consult_call_event(
        role=args.role,
        mode="pipeline",
        question=args.question,
        selected_models=final_selected_models,
        used_models=final_used_models,
        selection_path=reviewer_selection_path,
        pipeline_id=role_pipeline_id,
        context_files=args.context_files or [],
        output_file=args.output,
        cloud_only=args.cloud_only,
        success=True,
        error=None,
        duration_seconds=time.monotonic() - started_at,
        call_id=call_id,
        status=STATUS_SUCCESS,
        artifact_dir=str(artifact.run_dir),
    )
