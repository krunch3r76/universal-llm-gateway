"""Consult pipeline-branch helpers.

The code-review pipeline branch refuses. Shared artifact helpers stay for
the direct consult branch.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from consult_lib.core import ConsultResult
from consult_lib.history import (
    resolve_pipeline_models,
    write_consult_call_event,
    write_consult_call_started_event,
)
from consult_lib.pipeline import (
    CODE_REVIEW_PIPELINE_RETIRED,
)
from consult_lib.run_artifact import (
    STATUS_SELECTION_FAILED,
    RunArtifact,
    validate_file_lines,
    write_failure_marker,
    write_output_safe,
)




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



def _run_pipeline_branch(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    role_requirements: dict[str, Any],
    started_at: float,
    call_id: str,
    role_pipeline_id: str | None,
) -> None:
    """Refuse. This branch posted to the retired code-review pipeline."""
    del args, parser, role_requirements, started_at, call_id, role_pipeline_id
    raise SystemExit(CODE_REVIEW_PIPELINE_RETIRED)
