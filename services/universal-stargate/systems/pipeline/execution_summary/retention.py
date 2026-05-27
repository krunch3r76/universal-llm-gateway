"""
Filesystem retention for pipeline execution summaries.

Enforces the ``MAX_SUMMARIES_PER_PIPELINE`` retention policy across two
artifact types written under ``<output_dir>/<pipeline_id>/``:

- Top-level summary files (``.json`` / ``.yaml`` / ``.md``) written by the
  single-file write paths (``write_summary`` / ``_yaml`` / ``_markdown``).
- Execution sub-directories (``YYYYMMDD_HHMMSS_<exec8>/``) written by
  ``write_step_summaries`` — each contains per-step files plus a full summary.

The two are tracked independently: ``cleanup_old_summaries`` operates on files
only, ``cleanup_old_exec_dirs`` on directories only. ``cleanup_all_pipelines``
sweeps both for every pipeline at startup.

All three functions are pure-of-side-effects-except-filesystem: they take the
output root and retention cap as parameters; they do not own any state.
"""

from __future__ import annotations

from pathlib import Path

from universal_logging import get_logger

logger = get_logger(__name__)


def cleanup_old_summaries(
    output_dir: Path, pipeline_id: str, max_per_pipeline: int
) -> None:
    """
    Remove old top-level summary files for a pipeline.

    Keeps the ``max_per_pipeline`` most recent files (by mtime). Directories
    in the same parent are ignored — they are handled by
    ``cleanup_old_exec_dirs``. Unlink failures are logged at WARNING and do
    not abort the sweep.

    Args:
        output_dir: Root summaries directory (the writer's ``output_dir``).
        pipeline_id: Pipeline identifier — also the subdirectory name.
        max_per_pipeline: Retention cap.
    """
    pipeline_dir = output_dir / pipeline_id
    if not pipeline_dir.exists():
        return

    summary_files = sorted(
        [p for p in pipeline_dir.iterdir() if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    files_to_delete = summary_files[max_per_pipeline:]

    for filepath in files_to_delete:
        try:
            filepath.unlink()
            logger.debug(f"Deleted old summary: {filepath}")
        except Exception as e:
            logger.warning(f"Failed to delete {filepath}: {e}")


def cleanup_old_exec_dirs(
    output_dir: Path, pipeline_id: str, max_per_pipeline: int
) -> None:
    """
    Remove old execution sub-directories for a pipeline.

    Keeps the ``max_per_pipeline`` most recent (by mtime). Each directory is
    emptied (top-level files only, no recursion) and then rmdir'd. Failures
    are logged at WARNING and do not abort the sweep.

    Args:
        output_dir: Root summaries directory.
        pipeline_id: Pipeline identifier.
        max_per_pipeline: Retention cap.
    """
    pipeline_dir = output_dir / pipeline_id
    if not pipeline_dir.exists():
        return

    exec_dirs = sorted(
        [d for d in pipeline_dir.iterdir() if d.is_dir()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    dirs_to_delete = exec_dirs[max_per_pipeline:]

    for exec_dir in dirs_to_delete:
        try:
            for file_path in exec_dir.iterdir():
                file_path.unlink()
            exec_dir.rmdir()
            logger.debug(f"Deleted old execution directory: {exec_dir}")
        except Exception as e:
            logger.warning(f"Failed to delete {exec_dir}: {e}")


def cleanup_all_pipelines(output_dir: Path, max_per_pipeline: int) -> None:
    """
    Sweep both file-based and directory-based summaries for every pipeline.

    Used at service startup. Iterates over every pipeline subdirectory under
    ``output_dir`` and applies both ``cleanup_old_summaries`` and
    ``cleanup_old_exec_dirs``. Per-pipeline failures are logged at WARNING
    and do not abort the sweep.

    Args:
        output_dir: Root summaries directory.
        max_per_pipeline: Retention cap.
    """
    if not output_dir.exists():
        return

    pipeline_dirs = [d for d in output_dir.iterdir() if d.is_dir()]

    for pipeline_dir in pipeline_dirs:
        pipeline_id = pipeline_dir.name
        try:
            cleanup_old_summaries(output_dir, pipeline_id, max_per_pipeline)
            cleanup_old_exec_dirs(output_dir, pipeline_id, max_per_pipeline)
            logger.debug(f"Cleaned up summaries for pipeline: {pipeline_id}")
        except Exception as e:
            logger.warning(f"Failed to cleanup pipeline {pipeline_id}: {e}")

    logger.info(f"Startup cleanup complete: {len(pipeline_dirs)} pipeline(s) processed")
