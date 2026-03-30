"""Tee subprocess output to a log file while yielding summary lines to the TUI.

Wraps an ``AsyncIterator[str]`` (e.g. from ``build_image()`` or ``deploy_remote()``),
writes every line to a timestamped file under ``/tmp/logs/tui/``, and yields only
human-readable progress summaries so the TUI stays responsive without showing
raw Docker layer output.
"""

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

_LOG_DIR = Path("/tmp/logs/tui")

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
# BuildKit: "#5 [base-builder 1/4] ..." or "=> [stage-4 1/16] ..."
_BUILDKIT_STEP_RE = re.compile(r"(?:#\d+|=>)\s+\[(\S+)\s+(\d+)/(\d+)\]")
# Legacy Docker builder: "Step 3/16 : RUN ..."
_LEGACY_STEP_RE = re.compile(r"Step\s+(\d+)/(\d+)", re.IGNORECASE)

_RSYNC_CMD_RE = re.compile(r"^\$ rsync ")
_SCP_CMD_RE = re.compile(r"^\$ scp ")
_SSH_CMD_RE = re.compile(r"^\$ ssh ")


def _make_log_path(operation: str, host: str) -> Path:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    return _LOG_DIR / f"{operation}-{host}-{ts}.log"


def _buildkit_summary(stage: str, current: str, total: str) -> str | None:
    """Return a coarse stage summary for TUI display, or None to suppress."""
    if stage == "internal":
        return None
    if current == "1":
        return f"[{stage}] start"
    if current == total:
        return f"[{stage}] done ({total}/{total})"
    return None


def _legacy_summary(current: str, total: str) -> str | None:
    """Return a coarse legacy-builder summary for TUI display, or None to suppress."""
    if current == "1":
        return f"step {current}/{total}"
    if current == total:
        return f"step {current}/{total}"
    return None


async def tee_with_summary(
    source: AsyncIterator[str],
    *,
    operation: str,
    host: str = "localhost",
) -> AsyncIterator[str]:
    """Consume *source*, write every line to a log file, yield summaries.

    Args:
        source: Raw line iterator (build output, deploy output, etc.).
        operation: Short label for the log file name (e.g. ``"build"``, ``"deploy"``).
        host: Node hostname used in summary prefixes and the log filename.

    Yields:
        Human-readable progress lines prefixed with ``[host]``.
    """
    log_path = _make_log_path(operation, host)
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    last_step: str | None = None

    with log_path.open("w") as fh:
        async for line in source:
            fh.write(line + "\n")
            fh.flush()

            clean = _ANSI_RE.sub("", line) if "\x1b" in line else line

            bk = _BUILDKIT_STEP_RE.search(clean)
            if bk:
                progress = _buildkit_summary(bk.group(1), bk.group(2), bk.group(3))
                if progress is not None and progress != last_step:
                    last_step = progress
                    yield f"[{host}] Building... {progress}"
                continue

            legacy = _LEGACY_STEP_RE.search(clean)
            if legacy:
                progress = _legacy_summary(legacy.group(1), legacy.group(2))
                if progress is not None and progress != last_step:
                    last_step = progress
                    yield f"[{host}] Building... {progress}"
                continue

            if _RSYNC_CMD_RE.match(clean):
                yield f"[{host}] Syncing repository..."
                continue
            if _SCP_CMD_RE.match(clean):
                yield f"[{host}] Copying node env..."
                continue
            if _SSH_CMD_RE.match(clean):
                yield f"[{host}] Starting relay..."
                continue

            if clean.startswith("Build completed") or clean.startswith("Build FAILED"):
                yield f"[{host}] {clean}"
                continue
            if clean.startswith("Build cancelled"):
                yield f"[{host}] {clean}"
                continue
            if clean.startswith("ERROR:"):
                yield f"[{host}] {clean}"
                continue
            if "[red]" in clean:
                yield f"[{host}] {clean}"

    yield f"[{host}] Full log: {log_path}"
