"""GPU architecture detection."""
import logging
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)


class GPUDetector:
    """Detect GPU compute capability."""

    def detect(self) -> str:
        """
        Auto-detect GPU compute capability.

        Returns:
            GPU architecture code (e.g., "89" for SM_89, "120" for SM_120)

        Raises:
            RuntimeError: If no GPU detected

        Note:
            If multiple GPUs are present, uses the first GPU's architecture
            and logs a warning about the others.
        """
        # Try nvidia-smi first (most reliable)
        arch = self._detect_via_nvidia_smi()
        if arch:
            logger.info(f"✅ GPU detected via nvidia-smi: SM_{arch}")
            self._check_multiple_gpus()
            return arch

        # Fallback to PyTorch
        arch = self._detect_via_pytorch()
        if arch:
            logger.info(f"✅ GPU detected via PyTorch: SM_{arch}")
            self._check_multiple_gpus_pytorch()
            return arch

        # No GPU found
        raise RuntimeError(
            "❌ No NVIDIA GPU detected!\n"
            "   Tried: nvidia-smi, PyTorch\n"
            "   Use --gpu-generic for portable build or --gpu-arch=XX for manual specification"
        )

    def _detect_via_nvidia_smi(self) -> Optional[str]:
        """
        Detect via nvidia-smi.

        Returns:
            GPU architecture code or None if detection failed
        """
        try:
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=compute_cap', '--format=csv,noheader'],
                capture_output=True,
                text=True,
                check=True,
                timeout=5
            )

            # Parse output (e.g., "8.9" -> "89")
            compute_cap = result.stdout.strip().split('\n')[0]  # First GPU
            if compute_cap:
                arch = compute_cap.replace('.', '')
                logger.debug(f"nvidia-smi reported compute capability: {compute_cap} (SM_{arch})")
                return arch

        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
            logger.debug(f"nvidia-smi detection failed: {e}")

        return None

    def _detect_via_pytorch(self) -> Optional[str]:
        """
        Detect via PyTorch.

        Returns:
            GPU architecture code or None if detection failed
        """
        try:
            import torch

            if not torch.cuda.is_available():
                logger.debug("PyTorch: CUDA not available")
                return None

            # Get compute capability of first GPU
            major, minor = torch.cuda.get_device_capability(0)
            arch = f"{major}{minor}"

            device_name = torch.cuda.get_device_name(0)
            logger.debug(f"PyTorch detected: {device_name} (SM_{arch})")

            return arch

        except ImportError:
            logger.debug("PyTorch not installed")
        except Exception as e:
            logger.debug(f"PyTorch detection failed: {e}")

        return None

    def _check_multiple_gpus(self):
        """Check for multiple GPUs and log warning."""
        try:
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                capture_output=True,
                text=True,
                check=True,
                timeout=5
            )
            gpu_names = result.stdout.strip().split('\n')
            if len(gpu_names) > 1:
                logger.warning(f"⚠️  Multiple GPUs detected ({len(gpu_names)})")
                logger.warning("   Using first GPU for architecture detection")
                for i, name in enumerate(gpu_names):
                    logger.warning(f"   GPU {i}: {name}")
        except Exception:
            pass

    def _check_multiple_gpus_pytorch(self):
        """Check for multiple GPUs via PyTorch and log warning."""
        try:
            import torch
            gpu_count = torch.cuda.device_count()
            if gpu_count > 1:
                logger.warning(f"⚠️  Multiple GPUs detected ({gpu_count})")
                logger.warning("   Using first GPU for architecture detection")
                for i in range(gpu_count):
                    name = torch.cuda.get_device_name(i)
                    logger.warning(f"   GPU {i}: {name}")
        except Exception:
            pass

    def get_gpu_name(self) -> Optional[str]:
        """Get GPU name for display."""
        try:
            result = subprocess.run(
                ['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'],
                capture_output=True,
                text=True,
                check=True,
                timeout=5
            )
            return result.stdout.strip().split('\n')[0]
        except Exception:
            return None

