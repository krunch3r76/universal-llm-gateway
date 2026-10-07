"""Spawn a job, watch its output, and fold the terminal transition.

There is no wall-clock deadline. Silence for ``idle_seconds`` sends
SIGTERM to the process group, waits ten seconds, then SIGKILL. Cancel of
an admitted run appends cancelled and does not signal. The runner re-reads
the fold before spawn so that cancel wins the race.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from typing import Any

from pydantic import BaseModel, ValidationError
from universal_logging import get_logger

from jobs import events
from jobs.delivery import Delivery
from jobs.journal import Journal
from jobs.spec import JobSpec, resolved_executable

logger = get_logger(__name__)

_GRACE_S = 10.0
_PROGRESS_INTERVAL_S = 5.0
_RAW_LIMIT = 4000


class Runner:
    """In-process supervisor for one jobs app. The journal is the authority."""

    def __init__(self, journal: Journal, delivery: Delivery, specs: dict[str, JobSpec]) -> None:
        self.journal = journal
        self.delivery = delivery
        self.specs = specs
        self._lock = asyncio.Lock()
        self._progress: dict[str, int] = {}
        self._epoch: dict[str, int] = {}
        self._last_emit: dict[str, float] = {}
        self.journal.add_listener(self._bump)

    def progress_seq(self, run_id: str) -> int:
        """Return the in-memory progress counter. Restart resets it to zero."""
        return self._progress.get(run_id, 0)

    def _bump(self, run_id: str) -> None:
        self._epoch[run_id] = self._epoch.get(run_id, 0) + 1

    async def wait_for_change(
        self, run_id: str, seconds: int, start_state: str, start_progress: int
    ) -> None:
        """Return on the next fold change or progress tick, or when seconds elapse."""
        if seconds <= 0:
            return
        deadline = time.monotonic() + seconds
        start_epoch = self._epoch.get(run_id, 0)
        while time.monotonic() < deadline:
            fold = self.journal.fold(run_id)
            if fold is not None and fold.state != start_state:
                return
            if self.progress_seq(run_id) != start_progress:
                return
            if self._epoch.get(run_id, 0) != start_epoch:
                return
            await asyncio.sleep(0.05)

    def schedule(self, run_id: str) -> None:
        """Start the spawn task. The caller has already appended admitted."""
        asyncio.create_task(self._run(run_id))

    async def cancel(self, run_id: str) -> tuple[int, str]:
        """Apply DELETE. Admitted becomes cancelled with no signal.

        Running appends cancelling and signals the process group. The
        terminal cancelled row is appended when the child exits. Repeat
        cancel on cancelling or cancelled is a no-op.
        """
        async with self._lock:
            fold = self.journal.fold(run_id)
            if fold is None:
                return 404, "missing"
            if fold.state == "admitted":
                self.journal.append(run_id, "cancelled", {"signal": "none"})
                return 200, "cancelled"
            if fold.state == "running":
                self.journal.append(run_id, "cancelling", {})
                pgid = fold.data.get("pgid")
                if isinstance(pgid, int):
                    asyncio.create_task(_kill_and_wait(pgid))
                return 202, "cancelling"
            if fold.state in {"cancelling", "cancelled"}:
                return 200, fold.state
            return 409, "run_terminal"

    async def _run(self, run_id: str) -> None:
        fold = self.journal.fold(run_id)
        if fold is None or fold.state != "admitted":
            return
        spec = self.specs[fold.job]
        args = spec.args_model.model_validate(fold.args)
        if spec.pre_run is not None:
            try:
                await spec.pre_run(args, run_id)
            except Exception as exc:
                if self.journal.fold(run_id).state == "admitted":  # type: ignore[union-attr]
                    self.journal.append(
                        run_id,
                        "failed",
                        {"error": {"code": "pre_run_failed"}, "message": str(exc)},
                    )
                return
            if self.journal.fold(run_id).state != "admitted":  # type: ignore[union-attr]
                return
        try:
            await self._spawn(run_id, spec, args)
        except Exception as exc:
            logger.warning("jobs spawn failed run=%s error=%s", run_id, exc)
            current = self.journal.fold(run_id)
            if current is not None and current.state == "admitted":
                self.journal.append(
                    run_id,
                    "failed",
                    {"error": {"code": "spawn_failed"}, "message": str(exc)},
                )

    async def _spawn(self, run_id: str, spec: JobSpec, args: BaseModel) -> None:
        argv = list(spec.argv(args))
        if spec.name == "bus-reply-watch":
            state = self.journal.path.parent / "runs" / run_id / "state.json"
            state.parent.mkdir(parents=True, exist_ok=True)
            argv.extend(["--state-file", str(state)])
        exe = str(resolved_executable(spec))
        async with self._lock:
            current = self.journal.fold(run_id)
            if current is None or current.state != "admitted":
                return
            proc = await asyncio.create_subprocess_exec(
                exe,
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                cwd=_repo(),
            )
            pgid = os.getpgid(proc.pid)
            self.journal.append(
                run_id,
                "running",
                {"pid": proc.pid, "pgid": pgid},
            )
        await self._supervise(run_id, spec, proc, pgid)

    async def _supervise(
        self,
        run_id: str,
        spec: JobSpec,
        proc: asyncio.subprocess.Process,
        pgid: int,
    ) -> None:
        started = time.monotonic()
        last = started
        stdout = bytearray()
        log = self.journal.log_path(run_id).open("ab")
        idle_flag = asyncio.Event()

        async def pump(stream: asyncio.StreamReader | None, capture: bytearray | None) -> None:
            nonlocal last
            if stream is None:
                return
            while not idle_flag.is_set():
                try:
                    chunk = await asyncio.wait_for(stream.read(4096), timeout=0.2)
                except TimeoutError:
                    if time.monotonic() - last >= spec.idle_seconds:
                        idle_flag.set()
                    continue
                if not chunk:
                    return
                last = time.monotonic()
                log.write(chunk)
                log.flush()
                if capture is not None:
                    capture.extend(chunk)
                self._note_progress(run_id, len(chunk))

        try:
            await asyncio.gather(pump(proc.stdout, stdout), pump(proc.stderr, None))
            if idle_flag.is_set():
                sig = await _kill_and_wait(pgid)
                code = proc.returncode if proc.returncode is not None else -1
                self._finish_idle(run_id, spec, code, sig)
            else:
                code = await proc.wait()
                self._finish_exit(run_id, spec, code, stdout, started)
        finally:
            log.close()
        await self.delivery.deliver(run_id)

    def _note_progress(self, run_id: str, nbytes: int) -> None:
        seq = self._progress.get(run_id, 0) + 1
        self._progress[run_id] = seq
        self._bump(run_id)
        now = time.monotonic()
        if now - self._last_emit.get(run_id, 0.0) >= _PROGRESS_INTERVAL_S:
            self._last_emit[run_id] = now
            events.emit_progress(run_id=run_id, progress_seq=seq, nbytes=nbytes)

    def _finish_idle(self, run_id: str, spec: JobSpec, code: int, sig: str) -> None:
        fold = self.journal.fold(run_id)
        if fold is not None and fold.state == "cancelling":
            self.journal.append(run_id, "cancelled", {"signal": sig, "exit_code": code})
            return
        self.journal.append(
            run_id,
            "failed",
            {
                "exit_code": code,
                "error": {"code": "idle_timeout"},
                "signal": sig,
            },
        )

    def _finish_exit(
        self,
        run_id: str,
        spec: JobSpec,
        code: int,
        stdout: bytearray,
        started: float,
    ) -> None:
        fold = self.journal.fold(run_id)
        if fold is not None and fold.state == "cancelling":
            self.journal.append(run_id, "cancelled", {"signal": "SIGTERM", "exit_code": code})
            return
        duration_ms = int((time.monotonic() - started) * 1000)
        if code != 0:
            self.journal.append(
                run_id,
                "failed",
                {"exit_code": code, "error": {"code": "exit_nonzero"}, "duration_ms": duration_ms},
            )
            return
        parsed = _parse_result(spec, stdout)
        if parsed is None and spec.result_schema is not None:
            self.journal.append(
                run_id,
                "failed",
                {"exit_code": 0, "error": {"code": "result_invalid"}, "duration_ms": duration_ms},
            )
            return
        data: dict[str, Any] = {"exit_code": 0, "duration_ms": duration_ms}
        if parsed is not None:
            data["result"] = parsed
        elif stdout:
            text = stdout.decode("utf-8", errors="replace")[-_RAW_LIMIT:]
            data["result"] = {"raw": text}
        self.journal.append(run_id, "completed", data)


def _repo() -> str:
    from jobs.spec import repo_root

    return str(repo_root())


def _parse_result(spec: JobSpec, stdout: bytearray) -> dict[str, Any] | None:
    if spec.result_schema is None:
        return None
    lines = [line for line in stdout.decode("utf-8", errors="replace").splitlines() if line.strip()]
    if not lines:
        return None
    try:
        payload = json.loads(lines[-1])
        model = spec.result_schema.model_validate(payload)
    except (json.JSONDecodeError, ValidationError):
        return None
    return model.model_dump(mode="json", by_alias=True)


def _signal_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return


async def _kill_and_wait(pgid: int) -> str:
    _signal_group(pgid, signal.SIGTERM)
    deadline = time.monotonic() + _GRACE_S
    while time.monotonic() < deadline:
        if not _group_alive(pgid):
            return "SIGTERM"
        await asyncio.sleep(0.1)
    _signal_group(pgid, signal.SIGKILL)
    return "SIGKILL"


def _group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
