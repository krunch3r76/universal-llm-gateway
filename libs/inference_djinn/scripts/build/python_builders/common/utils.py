"""Shared utilities for Python builders."""

import logging
import shutil
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def run_command(
    cmd: list[str],
    cwd: Path | None = None,
    check: bool = True,
    timeout: int | None = None,
    capture_output: bool = False,
) -> subprocess.CompletedProcess:
    """
    Run a command with logging.

    Args:
        cmd: Command to run as list of strings
        cwd: Working directory
        check: Raise on non-zero exit code
        timeout: Timeout in seconds
        capture_output: Capture stdout/stderr

    Returns:
        CompletedProcess result
    """
    cmd_str = " ".join(str(c) for c in cmd)
    logger.debug(f"Running: {cmd_str}")

    return subprocess.run(
        cmd,
        cwd=cwd,
        check=check,
        timeout=timeout,
        capture_output=capture_output,
        text=True if capture_output else None,
    )


def git_clone_with_retry(
    repo_url: str,
    target_dir: Path,
    max_retries: int = 3,
    timeout: int = 600,
    recursive: bool = True,
) -> None:
    """
    Clone a git repository with retry logic.

    Args:
        repo_url: Repository URL
        target_dir: Target directory for clone
        max_retries: Maximum number of retries
        timeout: Timeout in seconds per attempt
        recursive: Clone submodules

    Raises:
        RuntimeError: If clone fails after all retries
    """
    for attempt in range(max_retries):
        try:
            cmd = ["git", "clone"]
            if recursive:
                cmd.append("--recursive")
            cmd.extend([repo_url, str(target_dir)])

            subprocess.run(cmd, check=True, timeout=timeout)
            logger.info("   ✅ Clone complete")
            return

        except subprocess.TimeoutExpired:
            logger.warning(f"   ⚠️  Clone attempt {attempt + 1}/{max_retries} timed out")
            if attempt < max_retries - 1:
                logger.info("   🔄 Retrying...")
                # Clean up partial clone
                if target_dir.exists():
                    shutil.rmtree(target_dir)
                time.sleep(2)
            else:
                raise RuntimeError(
                    f"❌ Failed to clone {repo_url} after {max_retries} attempts\n"
                    f"   Network timeout. Please check your internet connection."
                )

        except subprocess.CalledProcessError as e:
            logger.warning(
                f"   ⚠️  Clone attempt {attempt + 1}/{max_retries} failed: {e}"
            )
            if attempt < max_retries - 1:
                logger.info("   🔄 Retrying...")
                # Clean up partial clone
                if target_dir.exists():
                    shutil.rmtree(target_dir)
                time.sleep(2)
            else:
                raise
