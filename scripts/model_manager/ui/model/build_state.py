"""Build state - tracks Docker GPU image build status and config."""

import asyncio
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

logger = logging.getLogger(__name__)

IMAGE_NAME = "universal-llm-gateway"
IMAGE_TAG = "gpu"
FULL_IMAGE = f"{IMAGE_NAME}:{IMAGE_TAG}"


class BuildStatus(StrEnum):
    NOT_BUILT = "not_built"
    BUILT = "built"
    BUILDING = "building"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(slots=True, kw_only=True)
class ImageConfig:
    """Build configuration extracted from image labels."""

    cpu: str = ""
    gpu_arch: str = ""
    vllm: bool = False
    cuda: str = ""

    def summary(self) -> str:
        parts = []
        if self.cpu:
            parts.append(f"CPU={self.cpu}")
        if self.gpu_arch:
            parts.append(f"GPU={self.gpu_arch}")
        parts.append(f"vLLM={'yes' if self.vllm else 'no'}")
        if self.cuda:
            parts.append(f"CUDA {self.cuda}")
        return "  ".join(parts)


@dataclass(slots=True, kw_only=True)
class ImageInfo:
    status: BuildStatus
    image_id: str = ""
    created: str = ""
    size: str = ""
    config: ImageConfig = field(default_factory=ImageConfig)


class BuildState:
    """
    Check whether the Docker GPU image exists and builder-scoped cache size.

    Build operations are delegated to docker/scripts/build/build-gpu.sh
    by the ServiceController (not handled here).
    """

    def __init__(self, workspace_root: Path) -> None:
        self._root = workspace_root

    def check_image(self) -> ImageInfo:
        if not shutil.which("docker"):
            return ImageInfo(status=BuildStatus.UNKNOWN)
        try:
            result = subprocess.run(
                [
                    "docker",
                    "image",
                    "inspect",
                    FULL_IMAGE,
                    "--format",
                    "{{.ID}}\t{{.Created}}\t{{.Size}}",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return ImageInfo(status=BuildStatus.NOT_BUILT)

            parts = result.stdout.strip().split("\t")
            return ImageInfo(
                status=BuildStatus.BUILT,
                image_id=parts[0][:12] if parts else "",
                created=parts[1] if len(parts) > 1 else "",
                size=self._format_size(parts[2]) if len(parts) > 2 else "",
                config=self._read_labels(),
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning("Docker inspect failed: %s", e)
            return ImageInfo(status=BuildStatus.UNKNOWN)

    @staticmethod
    def _read_labels() -> ImageConfig:
        try:
            result = subprocess.run(
                [
                    "docker",
                    "image",
                    "inspect",
                    FULL_IMAGE,
                    "--format",
                    "{{json .Config.Labels}}",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return ImageConfig()
            labels = json.loads(result.stdout.strip())
            return ImageConfig(
                cpu=labels.get("cpu.optimization", ""),
                gpu_arch=labels.get("gpu.arch", ""),
                vllm=labels.get("vllm.enabled", "false") == "true",
                cuda=labels.get("gpu.cuda_version", ""),
            )
        except (
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
            FileNotFoundError,
        ) as e:
            logger.warning("Failed to read image labels: %s", e)
            return ImageConfig()

    def check_build_cache(self, target: str = "gateway") -> str:
        """Return human-readable build cache size for one workspace builder."""
        script = self._root / "scripts" / "build-cache.sh"
        try:
            result = subprocess.run(
                ["bash", str(script), "size", target],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return ""
            return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning("Failed to check build cache: %s", e)
            return ""

    async def prune_build_cache(self, target: str = "gateway") -> str:
        """Prune one dedicated buildx builder cache for this workspace."""
        script = self._root / "scripts" / "build-cache.sh"
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash",
                str(script),
                "prune",
                target,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await proc.communicate()
            output = stdout.decode(errors="replace").strip() if stdout else ""
            if proc.returncode == 0:
                return f"Prune complete.\n{output}"
            return f"Prune failed (exit {proc.returncode}).\n{output}"
        except FileNotFoundError:
            return "docker not found."

    @staticmethod
    def _format_size(size_str: str) -> str:
        try:
            size_bytes = int(size_str)
            gb = size_bytes / (1024**3)
            return f"{gb:.1f} GB"
        except ValueError:
            return size_str
