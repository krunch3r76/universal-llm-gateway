"""System resource validation."""
import logging
import shutil
import sys
from pathlib import Path

import psutil

logger = logging.getLogger(__name__)


class SystemChecker:
    """Validate system resources."""

    def validate_memory(self, max_jobs: int, ram_per_job_gb: int = 3):
        """
        Validate sufficient RAM available.

        Args:
            max_jobs: Number of parallel jobs
            ram_per_job_gb: RAM required per job (default 3 GB)

        Raises:
            RuntimeError: If insufficient RAM (in non-interactive mode)
        """
        available_ram_gb = psutil.virtual_memory().available / (1024**3)
        required_ram_gb = max_jobs * ram_per_job_gb

        logger.info("💾 Memory check:")
        logger.info(f"   Available: {available_ram_gb:.1f} GB")
        logger.info(f"   Required: ~{required_ram_gb} GB ({max_jobs} jobs × {ram_per_job_gb} GB)")

        if required_ram_gb > available_ram_gb:
            safe_jobs = int(available_ram_gb // ram_per_job_gb)

            logger.warning("")
            logger.warning(f"⚠️  WARNING: Insufficient RAM for {max_jobs} parallel jobs!")
            logger.warning(f"   Available: {available_ram_gb:.1f} GB")
            logger.warning(f"   Required: ~{required_ram_gb} GB")
            logger.warning("")
            logger.warning("Recommendations:")
            logger.warning(f"  1. Reduce jobs: --jobs={safe_jobs} (safe based on available RAM)")
            logger.warning("  2. Close other applications to free memory")
            logger.warning("  3. Add swap space (slower but prevents OOM)")
            logger.warning("")

            # In interactive mode, prompt user
            if sys.stdin.isatty():
                response = input("Continue anyway? (yes/no): ").strip().lower()
                if response not in ('yes', 'y'):
                    raise RuntimeError(f"Build aborted. Use --jobs={safe_jobs} for safe build.")
                logger.warning("⚠️  Proceeding with insufficient RAM (user confirmed)")
            else:
                logger.warning("⚠️  Non-interactive mode - proceeding with warning")
        else:
            logger.info(f"   ✅ Sufficient RAM for {max_jobs} parallel jobs")

    def validate_disk_space(self, required_gb: int = 20):
        """
        Validate sufficient disk space.

        Args:
            required_gb: Minimum required disk space in GB
        """
        usage = shutil.disk_usage(Path.cwd())
        available_gb = usage.free / (1024**3)

        logger.info(f"💿 Disk space: {available_gb:.1f} GB available")

        if available_gb < required_gb:
            logger.warning(f"⚠️  Low disk space: {available_gb:.1f} GB (recommend {required_gb} GB)")

    def get_cpu_info(self):
        """Get CPU information."""
        total_cpus = psutil.cpu_count(logical=True)
        physical_cpus = psutil.cpu_count(logical=False)

        logger.info(f"🖥️  CPU: {total_cpus} threads ({physical_cpus} cores)")

        return {
            'total_cpus': total_cpus,
            'physical_cpus': physical_cpus,
        }

