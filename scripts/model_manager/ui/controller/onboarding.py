"""Onboarding controller - orchestrates download and measurement workflows."""

import asyncio
import logging
import sys
from collections.abc import AsyncIterator, Callable
from pathlib import Path

from ..model.catalog_state import CatalogState, ModelInfo
from ..model.local_env import LocalEnv

logger = logging.getLogger(__name__)


class OnboardingController:
    """
    Coordinates model download and measurement.

    Download uses huggingface_hub directly (same logic as download_catalog.py).
    Measurement delegates to the existing CLI command via subprocess.
    """

    def __init__(
        self,
        catalog: CatalogState,
        local_env: LocalEnv,
        workspace_root: Path,
    ) -> None:
        self._catalog = catalog
        self._local_env = local_env
        self._workspace_root = workspace_root

    @property
    def _model_path(self) -> Path:
        """Live read — reflects MODEL_PATH_ROOT changes made in Settings."""
        return self._local_env.model_path_root

    def check_downloaded(self, model: ModelInfo) -> bool:
        """Check if a model file exists in any configured search path."""
        for search_path in self._local_env.model_search_paths:
            if model.hf_local_subdir:
                dest = search_path / model.hf_local_subdir / (model.hf_file or "")
                if dest.exists():
                    return True
            elif model.hf_file and (search_path / model.hf_file).exists():
                return True
            elif (
                not model.hf_file and model.name and (search_path / model.name).is_dir()
            ):
                return True
        return False

    def get_download_path(self, model: ModelInfo) -> Path:
        if model.hf_local_subdir:
            return self._model_path / model.hf_local_subdir / (model.hf_file or "")
        if model.hf_file:
            return self._model_path / model.hf_file
        return self._model_path / model.name

    async def download_model(self, model: ModelInfo) -> AsyncIterator[str]:
        """
        Download a model from HuggingFace, yielding progress lines.

        Uses huggingface_hub library (same as download_catalog.py).
        """
        if not model.hf_repo:
            yield f"ERROR: No HuggingFace repo defined for {model.model_id}"
            return

        self._model_path.mkdir(parents=True, exist_ok=True)
        yield f"Downloading {model.display_name} to {self._model_path}"
        yield f"  Repo: {model.hf_repo}"

        if model.format in ("hf", "awq", "gptq"):
            async for line in self._download_directory(model):
                yield line
        else:
            async for line in self._download_gguf(model):
                yield line

    async def _reload_gateway_catalog(self) -> tuple[bool, str]:
        """Trigger catalog reload so Gateway's in-memory state matches disk."""
        import httpx

        stargate_url = "http://localhost:9999"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{stargate_url}/gateway/catalog/reload", timeout=10.0
                )
                resp.raise_for_status()
                data = resp.json()
                count = data.get("models_count", "?")
                return True, f"Catalog reloaded ({count} models)"
        except httpx.HTTPError as e:
            logger.warning("Pre-measure catalog reload failed: %s", e)
            return False, f"Catalog reload failed: {e}"

    async def measure_model(
        self, model_id: str, *, contexts: str = "", cpu: bool = False
    ) -> AsyncIterator[str]:
        """
        Run measurement via the existing CLI, yielding output lines.

        Triggers a Gateway catalog reload first so the merge step
        starts from current disk state (not stale in-memory profiles).
        Delegates to `python -m scripts.model_manager measure`.
        """
        ok, msg = await self._reload_gateway_catalog()
        yield f"{'✓' if ok else '⚠'} {msg}"

        args = [
            sys.executable,
            "-u",
            "-m",
            "scripts.model_manager",
            "measure",
            model_id,
            "--update-catalog",
        ]
        if contexts:
            args.extend(["--contexts", contexts])
        if cpu:
            args.append("--cpu")

        yield f"$ {' '.join(args)}"
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(self._workspace_root),
        )
        assert process.stdout is not None
        async for raw_line in process.stdout:
            yield raw_line.decode(errors="replace").rstrip()
        exit_code = await process.wait()
        if exit_code == 0:
            yield "Measurement completed successfully."
        else:
            yield f"Measurement FAILED (exit code {exit_code})."

    async def _download_with_heartbeat(
        self,
        fn: Callable[[], str],
        label: str = "downloading",
        interval: int = 10,
    ) -> AsyncIterator[str]:
        """Run blocking download in a thread, yielding elapsed-time heartbeat."""
        task = asyncio.create_task(asyncio.to_thread(fn))
        elapsed = 0
        while not task.done():
            done_set, _ = await asyncio.wait({task}, timeout=interval)
            if not done_set:
                elapsed += interval
                minutes, seconds = divmod(elapsed, 60)
                time_str = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
                yield f"  Still {label}... ({time_str})"
        result = task.result()
        yield f"  Complete: {result}"

    async def _download_gguf(self, model: ModelInfo) -> AsyncIterator[str]:
        if not model.hf_file:
            yield f"ERROR: No GGUF file specified for {model.model_id}"
            return

        local_dir = (
            self._model_path / model.hf_local_subdir
            if model.hf_local_subdir
            else self._model_path
        )
        local_dir.mkdir(parents=True, exist_ok=True)

        dest = local_dir / Path(model.hf_file).name
        if dest.exists():
            yield f"Already exists: {dest}"
        else:
            yield f"  File: {model.hf_file}"
            yield f"  Size: {model.size_display}"
            from huggingface_hub import hf_hub_download

            try:
                async for line in self._download_with_heartbeat(
                    lambda: hf_hub_download(
                        repo_id=model.hf_repo,
                        filename=model.hf_file,
                        local_dir=str(local_dir),
                    ),
                ):
                    yield line
            except Exception as e:
                logger.error("GGUF download failed for %s: %s", model.model_id, e)
                yield f"ERROR: Download failed: {e}"
                return

        if model.is_vision_model and model.hf_mmproj_file:
            mmproj_dest = local_dir / Path(model.hf_mmproj_file).name
            if mmproj_dest.exists():
                yield f"mmproj already exists: {mmproj_dest}"
            else:
                yield f"  mmproj: {model.hf_mmproj_file}"
                from huggingface_hub import hf_hub_download

                try:
                    async for line in self._download_with_heartbeat(
                        lambda: hf_hub_download(
                            repo_id=model.hf_repo,
                            filename=model.hf_mmproj_file,
                            local_dir=str(local_dir),
                        ),
                        label="downloading mmproj",
                    ):
                        yield line
                except Exception as e:
                    logger.error("mmproj download failed for %s: %s", model.model_id, e)
                    yield f"ERROR: mmproj download failed: {e}"

    async def _download_directory(self, model: ModelInfo) -> AsyncIterator[str]:
        target = self._model_path / model.name
        if target.exists() and any(target.iterdir()):
            yield f"Already exists: {target}"
            return

        yield f"  Format: {model.format} (directory-based)"
        yield f"  Size: {model.size_display}"

        target.mkdir(parents=True, exist_ok=True)
        from huggingface_hub import snapshot_download

        try:
            async for line in self._download_with_heartbeat(
                lambda: snapshot_download(
                    repo_id=model.hf_repo,
                    local_dir=str(target),
                    ignore_patterns=["*.md", "*.txt"],
                ),
            ):
                yield line
        except Exception as e:
            logger.error("Directory download failed for %s: %s", model.model_id, e)
            yield f"ERROR: Download failed: {e}"
