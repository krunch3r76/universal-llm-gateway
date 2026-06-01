"""
Demo of new process_ipc architecture with ProcessSupervisor.

Shows the new naming and structured configuration approach.
"""

import asyncio

from process_ipc import (
    ProcessSupervisor,
    ResourceMonitoringConfig,
    SupervisorConfig,
    UnixSocketConfig,
)


async def demo_supervisor():
    """Demonstrate ProcessSupervisor usage with structured configuration."""

    print("=" * 80)
    print("Process IPC - New Architecture Demo")
    print("=" * 80)

    # Configure Unix socket transport
    transport_config = UnixSocketConfig(
        socket_path="/tmp/demo_worker.sock",
        timeout=30.0,
        retry_attempts=3,
        socket_permissions=0o600,
    )

    # Configure resource monitoring
    resource_config = ResourceMonitoringConfig(
        enable_resource_monitoring=True,
        enable_gpu_monitoring=True,
        monitoring_interval=5.0,
    )

    # Create process supervisor
    print("\n1. Creating ProcessSupervisor with Unix socket transport...")
    config = SupervisorConfig(transport=transport_config, resource=resource_config)
    supervisor = ProcessSupervisor(config)

    print("   ✓ ProcessSupervisor created")
    print("   - Transport: AsyncUnixTransport (from universal_transport)")
    print(f"   - Socket: {transport_config.socket_path}")
    print(f"   - Timeout: {transport_config.timeout}s")

    # Spawn worker (commented out since we need an actual worker script)
    # print("\n2. Spawning worker process...")
    # await supervisor.spawn(
    #     worker_id="demo_worker",
    #     command=["python", "examples/demo_worker_server.py"],
    # )
    # print("   ✓ Worker spawned")

    # Send command directly to supervisor (commented out - needs actual worker)
    # print("\n3. Sending command to worker...")
    # correlation_id = await supervisor.send_command({"action": "test"})
    # print(f"   ✓ Command sent with correlation_id: {correlation_id}")

    # Send another command via supervisor
    # print("\n4. Sending another command...")
    # correlation_id = await supervisor.send_command({"action": "process_data", "data":
    # "test"})
    # print(f"   ✓ Command sent (correlation_id: {correlation_id})")

    # Receive response via message pump
    # print("\n5. Receiving response...")
    # response = await supervisor._message_pump.await_correlation(correlation_id,
    # timeout=10.0)
    # print(f"   ✓ Response received: {response}")

    # Monitor resources
    # print("\n6. Monitoring resources...")
    # usage = await supervisor.get_resource_usage("demo_worker")
    # if usage:
    #     print(
    #         f"   - RAM: {usage.ram_used / 1024**3:.2f} GB "
    #         f"({usage.ram_percent:.1f}%)"
    #     )
    #     if usage.vram_used:
    #         print(
    #             f"   - VRAM: {usage.vram_used / 1024**3:.2f} GB "
    #             f"({usage.vram_percent:.1f}%)"
    #         )
    #
    # peaks = supervisor.get_peak_usage("demo_worker")
    # print(f"   - Peak RAM: {peaks['peak_ram_gb']:.2f} GB")
    # print(f"   - Peak VRAM: {peaks['peak_vram_gb']:.2f} GB")

    # Stop worker
    # print("\n7. Stopping worker...")
    # await supervisor.stop("demo_worker")
    # print("   ✓ Worker stopped")

    # Shutdown supervisor
    print("\n2. Shutting down supervisor...")
    await supervisor.shutdown()
    print("   ✓ Supervisor shutdown complete")

    print("\n" + "=" * 80)
    print("Demo complete!")
    print("=" * 80)


async def demo_comparison():
    """Show comparison between old and new architecture."""

    print("\n" + "=" * 80)
    print("Architecture Comparison")
    print("=" * 80)

    print("\n📦 OLD ARCHITECTURE:")
    print(
        "   from process_ipc import UnixSocketProcessManager, "
        "WorkerProcess, WorkerClient"
    )
    print("   ")
    print("   manager = UnixSocketProcessManager()")
    print("   await manager.start_process('worker1', command, socket_path)")
    print("   client = WorkerClient('worker1', socket_path)")
    print("   await client.connect()")

    print("\n🎯 NEW ARCHITECTURE:")
    print(
        "   from process_ipc import ProcessSupervisor, SupervisorConfig, "
        "UnixSocketConfig"
    )
    print("   ")
    print("   config = SupervisorConfig.from_socket_path('/tmp/worker.sock')")
    print("   # OR with full configuration:")
    print("   config = SupervisorConfig(")
    print("       transport=UnixSocketConfig(socket_path='/tmp/worker.sock')")
    print("   )")
    print("   supervisor = ProcessSupervisor(config)")
    print("   await supervisor.spawn('worker1', command)")
    print("   handle = supervisor.handle('worker1')")
    print("   await handle.send({'action': 'process'})")

    print("\n✨ KEY IMPROVEMENTS:")
    print("   1. ProcessSupervisor - Clear lifecycle management role")
    print("   2. WorkerServer - Clear server-side implementation")
    print("   3. WorkerHandle - Lightweight proxy for communication")
    print("   4. Structured configs - Type-safe configuration")
    print("   5. Transport injection - Easy to add new transports")
    print("   6. Minimal required fields - Only socket_path required")


if __name__ == "__main__":
    print("Running new architecture demo...\n")
    asyncio.run(demo_supervisor())
    asyncio.run(demo_comparison())
