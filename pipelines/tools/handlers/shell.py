"""
Shell step handler — executes commands inside the pipeline-tools sidecar.

Uses `docker exec --timeout` (Docker 27+) so the kill happens inside the
container, preventing orphaned processes.

Security: the sidecar runs with --network none, --read-only, --cap-drop ALL,
--user 1000:1000, --pids-limit 64. Commands cannot escape the sandbox.

Invariants:
- ∀ execute(): command comes from static YAML domain field (never templated with runtime data)
- ∀ non-zero exit: returns StepOutput with exit_code in json (does not raise)
- ∀ timeout: raises TimeoutError after docker exec --timeout kills the process
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, override

from systems.pipeline.core.handlers.protocol import AbstractStepHandler, StepOutput
from universal_logging import get_logger

if TYPE_CHECKING:
    from systems.pipeline.core.handlers.protocol import PipelineContext
    from systems.pipeline.core.schemas import StepConfig

logger = get_logger(__name__)

_SIDECAR_NAME = "pipeline-tools"


class ShellHandler(AbstractStepHandler):
    """Execute a shell command inside the pipeline-tools sidecar container.

    Domain fields (from pipeline YAML step config):
        command: str    — shell command to execute (required)
        workdir: str    — working directory inside container (default /workspace)
        timeout: int    — seconds before kill (default 30)
    """

    step_type: str = "shell_v1"

    @override
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        command: str = step.get_domain_field("command", "")
        if not command:
            raise ValueError(f"Step '{step.id}': missing required 'command' field")

        workdir: str = step.get_domain_field("workdir", "/workspace")
        timeout: int = step.get_domain_field("timeout", 30)

        proc = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            "--timeout",
            str(timeout),
            "--workdir",
            workdir,
            _SIDECAR_NAME,
            "sh",
            "-c",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout + 10
            )
        except TimeoutError:
            proc.kill()
            raise TimeoutError(
                f"Step '{step.id}': docker exec did not exit within "
                + f"{timeout + 10}s (container timeout was {timeout}s)"
            ) from None

        stdout = stdout_bytes.decode(errors="replace") if stdout_bytes else ""
        stderr = stderr_bytes.decode(errors="replace") if stderr_bytes else ""
        rc = proc.returncode or 0

        # Exit code 137 = SIGKILL (from docker exec --timeout)
        if rc == 137:
            raise TimeoutError(
                f"Step '{step.id}': command killed after {timeout}s timeout"
            )

        if rc != 0:
            logger.warning(
                "shell_v1 '%s': exit_code=%d, stderr=%s",
                step.id,
                rc,
                stderr[:200],
            )

        return StepOutput(
            raw=stdout,
            json={"exit_code": rc, "stderr": stderr},
        )

    @override
    def validate(self, step: StepConfig) -> list[str]:
        errors: list[str] = []
        if not step.get_domain_field("command"):
            errors.append(f"Step '{step.id}' missing required 'command' field")
        return errors
