#!/usr/bin/env python3
"""
CUDA Availability Monitor with Auto-Restart
Monitors docker container for CUDA availability and restarts on failure.

Usage:
  sudo python3 cuda-monitor-daemon.py --container universal-gateway-gpu

Install as systemd service:
  sudo cp cuda-monitor.service /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable cuda-monitor
  sudo systemctl start cuda-monitor
"""

import argparse
import logging
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Configuration
CHECK_INTERVAL = 300  # 5 minutes
CONSECUTIVE_FAILURES_THRESHOLD = 3
RESTART_COOLDOWN = 600  # 10 minutes between restarts
MAX_RESTARTS_PER_HOUR = 3
LOG_FILE = "/var/log/cuda-monitor.log"


class CudaMonitor:
    def __init__(self, container_name: str, dry_run: bool = False):
        self.container_name = container_name
        self.dry_run = dry_run
        self.consecutive_failures = 0
        self.last_restart_time: Optional[datetime] = None
        self.restart_count_hourly = 0
        self.restart_count_hourly_reset_time = datetime.now()

        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
        )
        self.logger = logging.getLogger(__name__)

    def check_cuda_availability(self) -> bool:
        """Check if CUDA is available in the container."""
        try:
            # Test 1: nvidia-smi
            result = subprocess.run(
                ["docker", "exec", self.container_name, "nvidia-smi"],
                capture_output=True,
                timeout=15,
                text=True,
            )
            if result.returncode != 0:
                self.logger.warning(f"nvidia-smi check failed: {result.stderr}")
                return False

            # Test 2: PyTorch CUDA
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    self.container_name,
                    "python3",
                    "-c",
                    "import torch; assert torch.cuda.is_available()",
                ],
                capture_output=True,
                timeout=20,
                text=True,
            )
            if result.returncode != 0:
                self.logger.warning(f"PyTorch CUDA check failed: {result.stderr}")
                return False

            self.logger.info("✅ CUDA availability check passed")
            return True

        except subprocess.TimeoutExpired:
            self.logger.error("❌ CUDA check timed out")
            return False
        except Exception as e:
            self.logger.error(f"❌ CUDA check error: {e}")
            return False

    def should_restart(self) -> bool:
        """Determine if container should be restarted based on policies."""
        # Check consecutive failures threshold
        if self.consecutive_failures < CONSECUTIVE_FAILURES_THRESHOLD:
            return False

        # Check restart cooldown
        if self.last_restart_time:
            time_since_last_restart = datetime.now() - self.last_restart_time
            if time_since_last_restart < timedelta(seconds=RESTART_COOLDOWN):
                self.logger.warning(
                    f"⏳ Restart throttled (cooldown: {RESTART_COOLDOWN - time_since_last_restart.seconds}s remaining)"
                )
                return False

        # Reset hourly counter if needed
        if datetime.now() - self.restart_count_hourly_reset_time > timedelta(hours=1):
            self.restart_count_hourly = 0
            self.restart_count_hourly_reset_time = datetime.now()

        # Check hourly restart limit
        if self.restart_count_hourly >= MAX_RESTARTS_PER_HOUR:
            self.logger.error(
                f"🚫 Restart limit reached ({MAX_RESTARTS_PER_HOUR}/hour). "
                "Manual intervention required."
            )
            self.send_alert("CRITICAL: CUDA monitor restart limit exceeded")
            return False

        return True

    def restart_container(self) -> bool:
        """Restart the Docker container."""
        if self.dry_run:
            self.logger.info(
                f"[DRY RUN] Would restart container: {self.container_name}"
            )
            return True

        try:
            self.logger.warning(f"🔄 Restarting container: {self.container_name}")

            result = subprocess.run(
                ["docker", "restart", self.container_name],
                capture_output=True,
                timeout=60,
                text=True,
            )

            if result.returncode == 0:
                self.logger.info(f"✅ Container restarted successfully")
                self.last_restart_time = datetime.now()
                self.restart_count_hourly += 1
                self.consecutive_failures = 0
                self.send_alert(
                    f"Container {self.container_name} restarted due to CUDA failure"
                )
                return True
            else:
                self.logger.error(f"❌ Container restart failed: {result.stderr}")
                return False

        except Exception as e:
            self.logger.error(f"❌ Container restart error: {e}")
            return False

    def send_alert(self, message: str):
        """Send alert notification (placeholder - implement based on your needs)."""
        # Options:
        # - Email via sendmail
        # - Slack webhook
        # - Discord webhook
        # - PagerDuty
        # - Log to syslog

        # For now, just log prominently
        self.logger.critical(f"🚨 ALERT: {message}")

        # Example: Send to syslog
        try:
            subprocess.run(
                ["logger", "-t", "cuda-monitor", f"ALERT: {message}"], timeout=5
            )
        except Exception:
            pass

    def run(self):
        """Main monitoring loop."""
        self.logger.info(
            f"🚀 CUDA Monitor started for container: {self.container_name}"
        )
        self.logger.info(f"   Check interval: {CHECK_INTERVAL}s")
        self.logger.info(f"   Failure threshold: {CONSECUTIVE_FAILURES_THRESHOLD}")
        self.logger.info(f"   Restart cooldown: {RESTART_COOLDOWN}s")
        self.logger.info(f"   Max restarts/hour: {MAX_RESTARTS_PER_HOUR}")

        if self.dry_run:
            self.logger.info("   🧪 DRY RUN MODE - no restarts will be performed")

        while True:
            try:
                # Check CUDA availability
                if self.check_cuda_availability():
                    self.consecutive_failures = 0
                else:
                    self.consecutive_failures += 1
                    self.logger.warning(
                        f"⚠️  CUDA unavailable "
                        f"(failures: {self.consecutive_failures}/{CONSECUTIVE_FAILURES_THRESHOLD})"
                    )

                    # Check if restart is needed
                    if self.should_restart():
                        self.restart_container()

                # Wait for next check
                time.sleep(CHECK_INTERVAL)

            except KeyboardInterrupt:
                self.logger.info("🛑 CUDA Monitor stopped by user")
                break
            except Exception as e:
                self.logger.error(f"❌ Monitoring error: {e}")
                time.sleep(CHECK_INTERVAL)


def main():
    parser = argparse.ArgumentParser(
        description="CUDA availability monitor with auto-restart"
    )
    parser.add_argument(
        "--container", required=True, help="Docker container name to monitor"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Dry run mode (no actual restarts)"
    )

    args = parser.parse_args()

    # Verify docker is available
    try:
        subprocess.run(["docker", "version"], capture_output=True, check=True)
    except Exception:
        print("ERROR: Docker is not available or not in PATH", file=sys.stderr)
        sys.exit(1)

    # Verify container exists
    try:
        result = subprocess.run(
            ["docker", "inspect", args.container], capture_output=True, check=True
        )
    except subprocess.CalledProcessError:
        print(f"ERROR: Container '{args.container}' not found", file=sys.stderr)
        sys.exit(1)

    # Run monitor
    monitor = CudaMonitor(args.container, dry_run=args.dry_run)
    monitor.run()


if __name__ == "__main__":
    main()
