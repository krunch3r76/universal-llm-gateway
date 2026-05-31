"""
HuggingFace source tracing module for inference_djinn.

Traces local models back to their HuggingFace origins.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


@dataclass
class HFSource:
    """HuggingFace source information."""

    repo: str
    file: str | None  # None for directory-based models
    size_bytes: int
    sha256: str | None
    verified: bool = False

    def to_download_section(self) -> dict[str, Any]:
        """Convert to catalog download section format."""
        result: dict[str, Any] = {
            "huggingface": {
                "repo": self.repo,
                "verified": self.verified,
            },
            "size_bytes": self.size_bytes,
        }

        if self.file:
            result["huggingface"]["file"] = self.file
        if self.sha256:
            result["sha256"] = self.sha256

        return result


class SourceTracer:
    """Trace HuggingFace source for local models."""

    def __init__(self, cache_timeout: int = 3600):
        """
        Initialize source tracer.

        Args:
            cache_timeout: Cache timeout in seconds for HF API responses
        """
        self._hf_api = None
        self._cache: dict[str, Any] = {}
        self.cache_timeout = cache_timeout

    @property
    def hf_api(self):
        """Lazy-load HuggingFace API client."""
        if self._hf_api is None:
            try:
                from huggingface_hub import HfApi

                self._hf_api = HfApi()
            except ImportError:
                raise ImportError(
                    "huggingface_hub not installed. Run: pip install huggingface-hub"
                )
        return self._hf_api

    def trace_huggingface(
        self,
        model_path: Path,
        format_type: str,
        model_name: str | None = None,
    ) -> HFSource | None:
        """
        Find HuggingFace repo for local model.

        Strategy:
        1. Search HuggingFace API by model name
        2. Match by file hash (SHA256) if exact match needed
        3. Return repo, file, size_bytes, sha256

        Args:
            model_path: Path to local model file or directory
            format_type: Model format (gguf, hf, awq, gptq)
            model_name: Optional model name for searching

        Returns:
            HFSource if found, None otherwise
        """
        model_path = Path(model_path)
        model_name = model_name or model_path.stem

        if format_type == "gguf":
            return self._trace_gguf(model_path, model_name)
        else:
            return self._trace_hf_directory(model_path, model_name, format_type)

    def _trace_gguf(self, model_path: Path, model_name: str) -> HFSource | None:
        """Trace GGUF file to HuggingFace source."""
        filename = model_path.name

        # Try common GGUF repo naming patterns
        search_terms = self._generate_search_terms(model_name, "gguf")

        for term in search_terms:
            results = self._search_repos(term, "gguf")
            for repo_id in results:
                source = self._verify_gguf_source(model_path, repo_id, filename)
                if source:
                    return source

        return None

    def _trace_hf_directory(
        self, model_path: Path, model_name: str, format_type: str
    ) -> HFSource | None:
        """Trace HF/AWQ/GPTQ model directory to HuggingFace source."""
        # Try common naming patterns
        search_terms = self._generate_search_terms(model_name, format_type)

        for term in search_terms:
            results = self._search_repos(term, format_type)
            for repo_id in results:
                source = self._verify_directory_source(model_path, repo_id)
                if source:
                    return source

        return None

    def _generate_search_terms(self, model_name: str, format_type: str) -> list[str]:
        """Generate search terms from model name."""
        terms = []

        # Clean up model name
        name = model_name.lower()
        name = name.replace("_", "-")

        # Remove common suffixes
        for suffix in ["-gguf", "-awq", "-gptq", "-4bit", "-8bit"]:
            name = name.removesuffix(suffix)

        # Remove quantization suffixes
        import re

        name = re.sub(r"-q[0-9]_?[a-z0-9_]*$", "", name, flags=re.IGNORECASE)
        name = re.sub(r"-[if]?q[0-9]+[a-z_]*$", "", name, flags=re.IGNORECASE)

        terms.append(name)

        # Add format-specific suffix
        if format_type == "gguf":
            terms.append(f"{name}-gguf")
        elif format_type == "awq":
            terms.append(f"{name}-awq")
        elif format_type == "gptq":
            terms.append(f"{name}-gptq")

        return terms

    def _search_repos(self, query: str, format_type: str) -> list[str]:
        """Search HuggingFace for repos matching query."""
        try:
            from huggingface_hub import list_models

            # Limit results for performance
            models = list(list_models(search=query, limit=10))

            repo_ids = []
            for model in models:
                repo_id = model.id
                repo_lower = repo_id.lower()

                # Filter by format hints in repo name
                if format_type == "gguf" and "gguf" in repo_lower:
                    repo_ids.append(repo_id)
                elif format_type == "awq" and "awq" in repo_lower:
                    repo_ids.append(repo_id)
                elif format_type == "gptq" and "gptq" in repo_lower:
                    repo_ids.append(repo_id)
                elif format_type == "hf":
                    # Standard HF models - avoid quantized repos
                    if not any(q in repo_lower for q in ["gguf", "awq", "gptq"]):
                        repo_ids.append(repo_id)

            return repo_ids

        except Exception as e:
            logger.warning(f"HuggingFace search failed: {e}")
            return []

    def _verify_gguf_source(
        self, local_path: Path, repo_id: str, filename: str
    ) -> HFSource | None:
        """
        Verify GGUF file against HuggingFace repo.

        Returns:
            HFSource if verified, None if hash mismatch or file not found.

        Raises:
            ConnectionError: If network is unavailable (let caller handle gracefully)
        """
        # Let network errors propagate - caller can handle gracefully
        repo_info = self.hf_api.repo_info(repo_id, files_metadata=True)
        siblings = repo_info.siblings or []

        # Find matching file by name
        for sibling in siblings:
            if sibling.rfilename.lower() == filename.lower():
                if sibling.lfs:
                    hf_sha256 = sibling.lfs.sha256
                    hf_size = sibling.lfs.size

                    # Quick size check first
                    local_size = local_path.stat().st_size
                    if local_size != hf_size:
                        logger.debug(f"Size mismatch: local={local_size}, HF={hf_size}")
                        return None  # Hash mismatch (size differs)

                    # Compute local SHA256
                    local_sha256 = self._compute_sha256(local_path)

                    if local_sha256 == hf_sha256:
                        return HFSource(
                            repo=repo_id,
                            file=sibling.rfilename,
                            size_bytes=hf_size,
                            sha256=hf_sha256,
                            verified=True,
                        )
                    else:
                        logger.debug(
                            f"SHA256 mismatch: local={local_sha256[:16]}..., "
                            f"HF={hf_sha256[:16]}..."
                        )
                        return None  # Hash mismatch

        logger.debug(f"File {filename} not found in repo {repo_id}")
        return None  # File not found in repo

    def _verify_directory_source(
        self, local_path: Path, repo_id: str
    ) -> HFSource | None:
        """
        Verify model directory against HuggingFace repo.

        Returns:
            HFSource if verified, None if hash mismatch or files not found.

        Raises:
            ConnectionError: If network is unavailable (let caller handle gracefully)
        """
        # Let network errors propagate - caller can handle gracefully
        repo_info = self.hf_api.repo_info(repo_id, files_metadata=True)
        siblings = repo_info.siblings or []

        # Build map of HF files
        hf_files = {s.rfilename: s for s in siblings if s.lfs}

        if not hf_files:
            logger.debug(f"No LFS files found in repo {repo_id}")
            return None

        # Check local files against HF files
        matched = 0
        total_size = 0
        largest_sha256 = None
        largest_size = 0

        for local_file in local_path.rglob("*"):
            if not local_file.is_file():
                continue

            rel_path = local_file.relative_to(local_path).as_posix()
            if rel_path not in hf_files:
                continue

            sibling = hf_files[rel_path]
            if not sibling.lfs:
                continue

            local_size = local_file.stat().st_size
            if local_size != sibling.lfs.size:
                logger.debug(
                    f"Size mismatch for {rel_path}: local={local_size}, "
                    f"HF={sibling.lfs.size}"
                )
                return None  # Size mismatch = not from this repo

            local_sha256 = self._compute_sha256(local_file)
            if local_sha256 != sibling.lfs.sha256:
                logger.debug(f"SHA256 mismatch for {rel_path}")
                return None  # Hash mismatch

            matched += 1
            total_size += local_size

            if local_size > largest_size:
                largest_size = local_size
                largest_sha256 = local_sha256

        if matched == 0:
            logger.debug(f"No matching files found in {local_path}")
            return None

        return HFSource(
            repo=repo_id,
            file=None,
            size_bytes=total_size,
            sha256=largest_sha256,
            verified=True,
        )

    def verify_against_repo(
        self,
        local_path: Path,
        repo_id: str,
        filename: str | None = None,
    ) -> HFSource | None:
        """
        Verify a local model against a specific HuggingFace repo.

        Args:
            local_path: Path to local model
            repo_id: HuggingFace repo ID (e.g., "user/model-name")
            filename: Filename in repo (required for GGUF)

        Returns:
            HFSource if verified, None otherwise
        """
        local_path = Path(local_path)

        if local_path.is_file():
            if not filename:
                filename = local_path.name
            return self._verify_gguf_source(local_path, repo_id, filename)
        else:
            return self._verify_directory_source(local_path, repo_id)

    def get_repo_files(self, repo_id: str) -> list[dict[str, Any]]:
        """
        Get list of files in a HuggingFace repo.

        Args:
            repo_id: HuggingFace repo ID

        Returns:
            List of file info dicts with name, size, sha256
        """
        try:
            repo_info = self.hf_api.repo_info(repo_id, files_metadata=True)
            siblings = repo_info.siblings or []

            files = []
            for sibling in siblings:
                file_info: dict[str, Any] = {"name": sibling.rfilename}
                if sibling.lfs:
                    file_info["size_bytes"] = sibling.lfs.size
                    file_info["sha256"] = sibling.lfs.sha256
                files.append(file_info)

            return files

        except Exception as e:
            logger.warning(f"Failed to get repo files for {repo_id}: {e}")
            return []

    @staticmethod
    def _compute_sha256(file_path: Path, chunk_size: int = 8192 * 1024) -> str:
        """Compute SHA256 hash of a file."""
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(chunk_size):
                sha256.update(chunk)
        return sha256.hexdigest()

    @staticmethod
    def compute_local_sha256(path: Path) -> str:
        """Public method to compute SHA256 of a file."""
        return SourceTracer._compute_sha256(path)
