"""CPU architecture detection."""
import logging
import platform
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)


class CPUDetector:
    """Detect CPU capabilities (x86-64 only)."""

    def detect_architecture(self) -> str:
        """
        Detect CPU microarchitecture level.

        Returns:
            Architecture level: "native", "x86-64-v4", "x86-64-v3", "x86-64-v2"

        Raises:
            RuntimeError: If non-x86-64 architecture detected

        Note:
            This detector only supports x86-64 architecture.
            ARM/AArch64 architectures are not supported.
        """
        # Check platform
        machine = platform.machine().lower()
        if machine not in ('x86_64', 'amd64'):
            raise RuntimeError(
                f"❌ Unsupported CPU architecture: {machine}\n"
                f"   This build system only supports x86-64 architecture.\n"
                f"   ARM/AArch64 builds are not currently supported."
            )

        # Check for AVX-512 (x86-64-v4)
        if self._has_avx512():
            logger.info("CPU supports x86-64-v4 (AVX-512)")
            return "x86-64-v4"

        # Check for AVX2 (x86-64-v3)
        if self._has_avx2():
            logger.info("CPU supports x86-64-v3 (AVX2)")
            return "x86-64-v3"

        # Fallback to x86-64-v2
        logger.info("CPU supports x86-64-v2 (baseline)")
        return "x86-64-v2"

    def _has_avx512(self) -> bool:
        """Check if CPU has AVX-512 support."""
        try:
            result = subprocess.run(
                ['grep', '-q', 'avx512', '/proc/cpuinfo'],
                capture_output=True
            )
            return result.returncode == 0
        except Exception:
            return False

    def _has_avx2(self) -> bool:
        """Check if CPU has AVX2 support."""
        try:
            result = subprocess.run(
                ['grep', '-q', 'avx2', '/proc/cpuinfo'],
                capture_output=True
            )
            return result.returncode == 0
        except Exception:
            return False

    def get_cpu_name(self) -> Optional[str]:
        """Get CPU model name for display."""
        try:
            result = subprocess.run(
                ['grep', '^model name', '/proc/cpuinfo'],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                if lines:
                    # Parse "model name : Intel(R) Core(TM) i9-13900K"
                    return lines[0].split(':', 1)[1].strip()
        except Exception:
            pass

        return platform.processor() or "Unknown"

