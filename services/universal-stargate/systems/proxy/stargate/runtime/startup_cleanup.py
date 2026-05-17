from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from universal_logging import get_logger

from systems.pipeline.execution_summary import get_summary_writer

logger = get_logger(__name__)


def _clear_snapshot_files_preserve_directories(snapshot_dir: Path) -> None:
    """
    Clear snapshot files while preserving standard stage directories.

    Preserves only standard stage directories (before/, after/, response-from-gateway/,
    response-to-client/). Deletes old task directories and all files.

    Rationale: If a human shells into a standard subdirectory, deleting it on restart
    orphans their cwd and forces re-navigation. Preserving standard directories avoids
    this UX footgun while clearing old content.
    """
    import shutil

    # Standard stage directories created by write_request_snapshot()
    standard_stages = {"before", "after", "response-from-gateway", "response-to-client"}

    for item in snapshot_dir.iterdir():
        if not item.is_dir():
            # Delete files and symlinks at top level.
            item.unlink()
            continue
        if item.name not in standard_stages:
            # Delete non-standard directories (old task dirs).
            shutil.rmtree(item)
            continue
        # Preserve standard stage directory, delete only its contents.
        for subitem in item.iterdir():
            if subitem.is_dir():
                # Delete unexpected nested directories in stage dirs.
                shutil.rmtree(subitem)
            else:
                subitem.unlink()


def _run_startup_cleanup(name: str, cleanup_func: Callable[[], None]) -> None:
    """Run startup cleanup with consistent logging and failure visibility."""
    try:
        cleanup_func()
        logger.info("Cleared %s on startup", name)
    except Exception as e:
        logger.warning(
            "Failed to cleanup %s on startup: %s",
            name,
            e,
            exc_info=True,
        )


def cleanup_startup_artifacts() -> None:
    """
    Run deterministic startup cleanup of local runtime artifacts.

    Preserves request snapshot stage dirs (before/, after/, etc.).
    Runs pipeline summary, snapshot, and failure cleanup.
    Failures logged but do not abort startup.
    """
    # Cleanup old pipeline summaries on startup
    _run_startup_cleanup(
        "pipeline summaries",
        lambda: get_summary_writer().cleanup_all_pipelines(),
    )

    # Cleanup request snapshots on startup
    data_dir = os.getenv("DATA_DIR", "/tmp")
    snapshot_dir = Path(data_dir) / "stargate-request-snapshots"
    if snapshot_dir.exists():
        _run_startup_cleanup(
            "request snapshots",
            lambda: _clear_snapshot_files_preserve_directories(snapshot_dir),
        )

    # Cleanup pipeline failures on startup
    log_dir = os.getenv("LOG_DIR", "/tmp/logs/universal-stargate")
    failures_dir = Path(log_dir) / "pipeline_failures"
    if failures_dir.exists():
        import shutil

        def _cleanup_failures_dir() -> None:
            shutil.rmtree(failures_dir)
            failures_dir.mkdir(parents=True, exist_ok=True)

        _run_startup_cleanup("pipeline failures", _cleanup_failures_dir)
