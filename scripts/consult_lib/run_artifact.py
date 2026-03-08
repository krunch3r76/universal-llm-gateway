"""Per-run artifact writer for consult invocations.

∀ consult run → structured directory under tmp/consult-runs/<ts>-<call_id>/
  metadata.json  — machine-readable run summary
  stdout.md      — final formatted output (written atomically on success)
  stderr.log     — captured stderr lines
  partial.json   — intermediate/partial results captured before failure

Later agents should read:
  1. metadata.json  (status, models, execution_id, output_path, partial_success)
  2. stdout.md       (final output when success=True)
  3. partial.json    (analyst/reviewer intermediate outputs when partial_success=True)
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .history import PipelineStepRecord

_RUNS_ROOT = Path("tmp/consult-runs")

# Valid explicit status values (not bool flags).
# ∀ run: exactly one status is set.
STATUS_SUCCESS = "success"
STATUS_PIPELINE_FAILED = "pipeline_failed"
STATUS_SELECTION_FAILED = "selection_failed"
STATUS_PARTIAL_OUTPUT_AVAILABLE = "partial_output_available"
STATUS_STALE_OUTPUT_PREVENTED = "stale_output_prevented"
STATUS_COMMAND_FAILED = "command_failed"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _runs_root() -> Path:
    """Resolve runs root; env override for testing."""
    configured = os.getenv("CONSULT_RUNS_DIR", "").strip()
    return Path(configured) if configured else _RUNS_ROOT


@dataclass(slots=True, kw_only=True)
class RunArtifact:
    """Mutable run state; written to disk at key checkpoints."""

    call_id: str
    role: str
    mode: str
    question: str
    pipeline_id: str | None = None
    selected_models: list[str] = field(default_factory=list)
    used_models: list[str] = field(default_factory=list)
    pipeline_virtual_model: str | None = None
    execution_id: str | None = None
    output_path: str | None = None
    status: str = STATUS_COMMAND_FAILED
    error_summary: str | None = None
    partial_outputs: list[dict[str, Any]] = field(default_factory=list)
    chain_trace: list[dict[str, Any]] = field(default_factory=list)
    stderr_lines: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=_now_iso)
    finished_at: str | None = None
    duration_seconds: float | None = None
    _run_dir: Path | None = field(default=None, repr=False)

    def _ensure_dir(self) -> Path:
        if self._run_dir is not None:
            return self._run_dir
        ts = self.started_at.replace(":", "-").replace(".", "-")
        name = f"{ts}-{self.call_id[:8]}"
        d = _runs_root() / name
        d.mkdir(parents=True, exist_ok=True)
        self._run_dir = d
        return d

    @property
    def run_dir(self) -> Path:
        return self._ensure_dir()

    def record_stderr(self, line: str) -> None:
        self.stderr_lines.append(line)

    def record_partial(
        self,
        *,
        step: str,
        model_id: str,
        content: str,
        execution_id: str | None = None,
    ) -> None:
        """Capture an intermediate step output (analyst, reviewer, etc.)."""
        self.partial_outputs.append(
            {
                "step": step,
                "model_id": model_id,
                "content": content,
                "execution_id": execution_id,
                "ts": _now_iso(),
            }
        )

    def record_chain_phase(self, phase_result: dict[str, Any]) -> None:
        """Append a per-phase trace entry for later agent recovery.

        Captures timing, model, prompts, and response previews from a chain
        phase result dict as produced by query_chain().
        """
        self.chain_trace.append(
            {
                "phase_index": phase_result.get("phase_index"),
                "phase": phase_result.get("phase"),
                "model_id": phase_result.get("model_id"),
                "started_at": phase_result.get("started_at"),
                "finished_at": phase_result.get("finished_at"),
                "duration_ms": phase_result.get("duration_ms"),
                "input_prompt_preview": phase_result.get("input_prompt_preview"),
                "response_preview": phase_result.get("response_preview"),
                "error": phase_result.get("error"),
            }
        )

    def record_partial_step(self, record: PipelineStepRecord) -> None:
        """Append a pipeline recorder step to partial_outputs.

        ``source`` is set to ``pipeline_recorder`` to distinguish these from
        manually captured partial outputs written via ``record_partial()``.
        """
        self.partial_outputs.append(
            {
                "source": "pipeline_recorder",
                "execution_id": record.execution_id,
                "pipeline_id": record.pipeline_id,
                "step": record.step_name,
                "event_type": record.event_type,
                "model_id": record.model_id,
                "raw": record.raw,
                "json_data": record.json_data,
                "prompt_tokens": record.prompt_tokens,
                "completion_tokens": record.completion_tokens,
                "latency_ms": record.latency_ms,
                "system_prompt": record.system_prompt,
                "user_prompt": record.user_prompt,
                "request_body": record.request_body,
                "error": record.error,
                "wall_clock": record.wall_clock,
            }
        )

    def _metadata(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "role": self.role,
            "mode": self.mode,
            "question_preview": self.question.strip().replace("\n", " ")[:200],
            "pipeline_id": self.pipeline_id,
            "pipeline_virtual_model": self.pipeline_virtual_model,
            "selected_models": self.selected_models,
            "used_models": self.used_models,
            "execution_id": self.execution_id,
            "output_path": self.output_path,
            "status": self.status,
            "error_summary": self.error_summary,
            "partial_success": bool(self.partial_outputs),
            "chain_phase_count": len(self.chain_trace) if self.chain_trace else None,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "run_dir": str(self.run_dir),
        }

    def checkpoint(self) -> None:
        """Write current metadata + partial outputs to disk (call at any time)."""
        d = self._ensure_dir()
        _atomic_write(d / "metadata.json", json.dumps(self._metadata(), indent=2))
        if self.partial_outputs:
            _atomic_write(
                d / "partial.json",
                json.dumps(self.partial_outputs, indent=2),
            )
        if self.chain_trace:
            _atomic_write(
                d / "chain_trace.json",
                json.dumps(self.chain_trace, indent=2),
            )
        if self.stderr_lines:
            (d / "stderr.log").write_text(
                "\n".join(self.stderr_lines), encoding="utf-8"
            )

    def finalize(
        self,
        *,
        status: str,
        output_text: str | None = None,
        error_summary: str | None = None,
        used_models: list[str] | None = None,
        execution_id: str | None = None,
        duration_seconds: float | None = None,
    ) -> Path:
        """Write final artifacts and return the run directory.

        stdout.md is written atomically only on success or partial_output_available.
        metadata.json is always written.
        """
        self.status = status
        self.error_summary = error_summary
        self.finished_at = _now_iso()
        if duration_seconds is not None:
            self.duration_seconds = round(duration_seconds, 3)
        if used_models is not None:
            self.used_models = used_models
        if execution_id is not None:
            self.execution_id = execution_id

        d = self._ensure_dir()
        _atomic_write(d / "metadata.json", json.dumps(self._metadata(), indent=2))

        if output_text and status in (STATUS_SUCCESS, STATUS_PARTIAL_OUTPUT_AVAILABLE):
            _atomic_write(d / "stdout.md", output_text)

        if self.partial_outputs:
            _atomic_write(
                d / "partial.json",
                json.dumps(self.partial_outputs, indent=2),
            )

        if self.chain_trace:
            _atomic_write(
                d / "chain_trace.json",
                json.dumps(self.chain_trace, indent=2),
            )

        if self.stderr_lines:
            (d / "stderr.log").write_text(
                "\n".join(self.stderr_lines), encoding="utf-8"
            )

        return d


def _atomic_write(path: Path, content: str) -> None:
    """Write content to path atomically (tmp file → rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_output_safe(
    *,
    path: Path,
    content: str,
    artifact: RunArtifact | None = None,
) -> None:
    """Atomically write content to -o path.

    If the write is for a run that has an artifact, also store output_path
    so metadata.json reflects the resolved location.

    Never leaves a stale partial file: content is written atomically.
    If content is empty (failure scenario), the caller must NOT call this —
    the old file is left in place with a stale marker appended instead.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, content)
    if artifact is not None:
        artifact.output_path = str(path.resolve())


def write_failure_marker(
    *,
    path: Path,
    call_id: str,
    status: str,
    error_summary: str,
    run_dir: str | None,
) -> None:
    """Overwrite -o path with a failure wrapper so stale content is not silently reused.

    Agents reading the -o path will see a FAILED header rather than old output.
    """
    lines = [
        "# Consult Run: FAILED",
        "",
        f"**Status**: `{status}`",
        f"**Call ID**: `{call_id}`",
        f"**Error**: {error_summary}",
    ]
    if run_dir:
        lines += ["", f"**Run artifacts**: `{run_dir}`"]
    lines += [
        "",
        "_This file was written by a failed consult run to prevent stale reuse._",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, "\n".join(lines))


# ---------------------------------------------------------------------------
# Scoping-output validation (FILE: line extraction + path existence checks)
# ---------------------------------------------------------------------------


def validate_file_lines(
    output: str,
    *,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Scan output for 'FILE: ...' lines and validate path existence.

    Returns:
        {
            "valid": [str, ...],       # paths that exist relative to repo_root
            "invalid": [str, ...],     # paths that do not exist
            "normalized": [str, ...],  # resolved repo-relative forms for valid paths
        }
    """
    root = repo_root or Path.cwd()
    valid: list[str] = []
    invalid: list[str] = []
    normalized: list[str] = []

    for line in output.splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith("FILE:"):
            continue
        raw_path = stripped[5:].strip().rstrip(".,;:")
        if not raw_path:
            continue
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = root / raw_path
        if candidate.exists():
            valid.append(raw_path)
            try:
                normalized.append(str(candidate.relative_to(root)))
            except ValueError:
                normalized.append(str(candidate))
        else:
            invalid.append(raw_path)

    return {"valid": valid, "invalid": invalid, "normalized": normalized}
