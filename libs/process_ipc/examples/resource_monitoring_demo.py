#!/usr/bin/env python3
"""
Resource Monitoring Demo

Demonstrates process resource monitoring with process_ipc.
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from process_ipc import (
    ProcessCoordinator,
    ProcessResourceUsage,
    ResourceMonitoringConfig,
)


def format_bytes(bytes_val):
    """Format bytes to human-readable string."""
    if bytes_val is None:
        return "N/A"

    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_val < 1024.0:
            return f"{bytes_val:.2f} {unit}"
        bytes_val /= 1024.0
    return f"{bytes_val:.2f} PB"


def on_resource_update(usage: ProcessResourceUsage):
    """Callback for resource updates."""
    print(
        f"\n[{usage.timestamp.strftime('%H:%M:%S')}] Resource Update: {usage.process_id}"
    )
    print(f"  RAM: {format_bytes(usage.ram_used)} ({usage.ram_percent:.1f}%)")

    if usage.vram_used:
        print(f"  VRAM: {format_bytes(usage.vram_used)} ({usage.vram_percent:.1f}%)")

    print(f"  CPU: {usage.cpu_percent:.1f}%")
    print(f"  Threads: {usage.num_threads}")

    # Alert on high usage
    if usage.ram_percent > 50.0:
        print("  ⚠️  High RAM usage!")

    if usage.vram_percent and usage.vram_percent > 50.0:
        print("  ⚠️  High VRAM usage!")


async def main():
    """Main demo function."""
    print("=" * 60)
    print("Process IPC - Resource Monitoring Demo")
    print("=" * 60)

    # Configure resource monitoring
    print("\n1. Configuring resource monitoring...")
    resource_config = ResourceMonitoringConfig(
        enable_resource_monitoring=True,
        monitoring_interval=3.0,  # Check every 3 seconds
        history_size=50,
        enable_gpu_monitoring=True,
        on_resource_update=on_resource_update,
    )

    # Create coordinator
    print("2. Creating ProcessCoordinator...")
    coordinator = ProcessCoordinator(
        log_base_dir="/tmp/process_ipc_demo", resource_config=resource_config
    )

    # Check monitoring status
    status = coordinator.get_resource_monitoring_status()
    print("\n3. Monitoring Status:")
    print(f"   Enabled: {status['enabled']}")
    print(f"   Interval: {status['interval']}s")
    print(f"   GPU Available: {status['gpu_available']}")

    # Start a simple worker process (Python interpreter)
    print("\n4. Starting worker process...")
    process_id = "demo_worker"

    success = await coordinator.start_process(
        process_id=process_id,
        command=["python3", "-c", "import time; time.sleep(300)"],
        socket_path=f"/tmp/process_ipc_demo_{process_id}.sock",
    )

    if not success:
        print("Failed to start worker process!")
        return

    print("   Worker started successfully!")

    # Wait for initial resource collection
    await asyncio.sleep(5)

    # Get current resource usage
    print("\n5. Current Resource Usage:")
    usage = coordinator.get_resource_usage(process_id)
    if usage:
        print(f"   Process ID: {usage.process_id}")
        print(f"   PID: {usage.pid}")
        print(f"   RAM: {format_bytes(usage.ram_used)} ({usage.ram_percent:.1f}%)")
        if usage.vram_used:
            print(
                f"   VRAM: {format_bytes(usage.vram_used)} ({usage.vram_percent:.1f}%)"
            )
        else:
            print("   VRAM: No GPU detected or not in use")
        print(f"   CPU: {usage.cpu_percent:.1f}%")
        print(f"   Threads: {usage.num_threads}")
        print(
            f"   System RAM: {format_bytes(usage.system_ram_total)} total, {format_bytes(usage.system_ram_available)} available"
        )
    else:
        print("   No resource data available yet")

    # Monitor for a while
    print("\n6. Monitoring resources for 20 seconds...")
    print("   (Resource updates will be printed via callback)")

    try:
        for i in range(4):
            await asyncio.sleep(5)

            # Get resource history
            history = coordinator.get_resource_history(process_id, limit=5)
            if history:
                print(f"\n   Recent history ({len(history)} snapshots):")
                for snapshot in history[-3:]:
                    print(
                        f"     {snapshot.timestamp.strftime('%H:%M:%S')}: {format_bytes(snapshot.ram_used)} RAM"
                    )

    except KeyboardInterrupt:
        print("\n\nInterrupted by user")

    # Demonstrate on-demand collection
    print("\n7. On-demand resource collection...")
    fresh_usage = await coordinator.collect_resources_now(process_id)
    if fresh_usage:
        print(f"   Fresh snapshot: {format_bytes(fresh_usage.ram_used)} RAM")

    # Get all resources
    print("\n8. All monitored processes:")
    all_usage = coordinator.get_all_resource_usage()
    for pid, usage in all_usage.items():
        print(
            f"   {pid}: {format_bytes(usage.ram_used)} RAM, {usage.cpu_percent:.1f}% CPU"
        )

    # Cleanup
    print("\n9. Cleaning up...")
    await coordinator.stop_process(process_id, force=True)
    await coordinator.shutdown()

    print("\n" + "=" * 60)
    print("Demo completed!")
    print("=" * 60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nDemo interrupted by user")
    except Exception as e:
        print(f"\nError: {e}")
        import traceback

        traceback.print_exc()
