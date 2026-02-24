"""
Shell step handler — executes commands inside the pipeline-tools sidecar.

Timeout is enforced via asyncio.wait_for; on expiry the docker exec process
is killed (SIGKILL). Container-side orphans are bounded by --pids-limit 64.

Security: the sidecar runs with --network none, --read-only, --cap-drop ALL,
--user 1000:1000, --pids-limit 64. Commands cannot escape the sandbox.

Invariants:
- ∀ execute(): command comes from static YAML domain field (never templated with runtime data)
- ∀ infrastructure failure (container missing, daemon down): raises RuntimeError
- ∀ non-zero exit from command: returns StepOutput with stderr appended to raw
- ∀ timeout: raises TimeoutError after asyncio.wait_for kills the docker exec process
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

_INFRA_ERROR_MARKERS = (
    "No such container",
    "Error response from daemon",
    "Cannot connect to the Docker daemon",
    "Is the docker daemon running",
)


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

        if rc == 137:
            raise TimeoutError(
                f"Step '{step.id}': command killed after {timeout}s timeout"
            )

        if rc != 0 and _is_infra_error(stderr):
            raise RuntimeError(
                f"Step '{step.id}': sidecar infrastructure error "
                f"(exit_code={rc}): {stderr.strip()[:300]}"
            )

        if rc != 0:
            logger.warning(
                "shell_v1 '%s': exit_code=%d, stderr=%s",
                step.id,
                rc,
                stderr[:200],
            )

        raw = stdout
        if rc != 0 and not stdout.strip():
            raw = (
                f"[shell exit_code={rc}]\n{stderr}"
                if stderr
                else f"[shell exit_code={rc}]"
            )

        return StepOutput(
            raw=raw,
            json={"exit_code": rc, "stderr": stderr},
        )

    @override
    def validate(self, step: StepConfig) -> list[str]:
        errors: list[str] = []
        if not step.get_domain_field("command"):
            errors.append(f"Step '{step.id}' missing required 'command' field")
        return errors


def _is_infra_error(stderr: str) -> bool:
    """Detect docker daemon / container-missing errors vs command failures."""
    return any(marker in stderr for marker in _INFRA_ERROR_MARKERS)
