#!/usr/bin/env python3
"""
Demo for asynchronous streaming with fast cancellation.

This example demonstrates:
1. Concurrent message processing in the worker
2. Fast cancellation response (<100ms) during streaming
3. Automatic event loop yielding in v4.0.0
4. Measuring cancellation latency

Usage:
    python examples/async_streaming_demo.py
"""

import asyncio
import sys
import time
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from process_ipc import (
    ProcessSupervisor,
    SupervisorConfig,
    WorkerProcess,
)
from process_ipc.core import signals
from process_ipc.core.messages import create_message


# Define a simple worker that can stream data with cancellation support
class DemoWorker(WorkerProcess):
    async def process_command(self, command: dict) -> dict:
        """Process command with support for long-running streaming."""
        action = command.get("action")
        correlation_id = command.get("correlation_id", "unknown")

        if action == "stream_with_delay":
            # Simulate a long-running streaming operation
            # The automatic yielding in v4.0.0 ensures cancellation is fast
            return await self._stream_with_delay(command, correlation_id)
        elif action == "echo":
            return {"result": f"Echoed: {command.get('data')}"}

        return {"status": "ok"}

    async def _stream_with_delay(self, command: dict, correlation_id: str):
        """
        Stream data with simulated processing delay.

        This demonstrates how automatic yielding in v4.0.0 enables
        fast cancellation even during long-running operations.
        """
        # Register cancellation event
        self._stream_cancellation_events[correlation_id] = asyncio.Event()

        try:
            max_chunks = command.get("max_chunks", 100)
            chunk_delay = command.get("chunk_delay", 0.3)  # 300ms per chunk

            for i in range(max_chunks):
                # Check for cancellation using the helper method
                if await self.check_cancellation(correlation_id):
                    self._logger.info(f"Stream {correlation_id} cancelled at chunk {i}")
                    return {"status": "cancelled", "chunks_sent": i}

                # Simulate processing time (e.g., inference)
                await asyncio.sleep(chunk_delay)

                # Send chunk
                await self.report_streaming_chunk(
                    correlation_id=correlation_id,
                    chunk_data={"text": f"Chunk {i}", "index": i},
                    chunk_number=i,
                )

            # Completed normally
            await self.report_streaming_complete(correlation_id, max_chunks)
            return {"status": "completed", "chunks_sent": max_chunks}

        finally:
            # Cleanup
            if correlation_id in self._stream_cancellation_events:
                del self._stream_cancellation_events[correlation_id]

    async def _initialize_worker(self) -> None:
        # Custom worker initialization logic
        self._logger.info(
            f"DemoWorker {self.worker_id} custom initialization complete."
        )
        self._status["model_loaded"] = True
        self._status["ready"] = True


async def async_streaming_demo():
    print("=" * 80)
    print("Process IPC v4.0.0 - Fast Cancellation Demo")
    print("=" * 80)
    print("\nThis demo shows how v4.0.0's automatic event loop yielding")
    print("enables <100ms cancellation response during long-running operations.")
    print()

    worker_id = "streaming_worker"
    socket_path = f"/tmp/process_ipc_{worker_id}.sock"

    # Create configuration
    config = SupervisorConfig.from_socket_path(socket_path)
    config.resource.enable_resource_monitoring = False

    print("1. Creating ProcessSupervisor...")
    supervisor = ProcessSupervisor(config)
    print("   ✓ ProcessSupervisor created")

    print(f"\n2. Spawning DemoWorker process (ID: {worker_id})...")
    # Create worker script
    worker_script_content = f"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from examples.async_streaming_demo import DemoWorker

async def main():
    worker = DemoWorker("{worker_id}", "{socket_path}")
    await worker.initialize("{socket_path}")
    await worker.run()

if __name__ == "__main__":
    asyncio.run(main())
"""
    worker_script_path = f"/tmp/demo_worker_script_{worker_id}.py"
    with open(worker_script_path, "w") as f:
        f.write(worker_script_content)

    command = [sys.executable, worker_script_path]

    success = await supervisor.spawn(
        worker_id=worker_id, command=command, startup_timeout=30.0
    )

    if not success:
        print("   ✗ Failed to spawn worker!")
        await supervisor.shutdown()
        return
    print("   ✓ Worker spawned and ready")

    print("\n3. Starting long-running streaming operation...")
    print("   (30 chunks × 300ms = 9 seconds if not cancelled)")

    # Start streaming command
    correlation_id = await supervisor.start_stream(
        {
            "action": "stream_with_delay",
            "max_chunks": 30,
            "chunk_delay": 0.3,  # 300ms per chunk
        }
    )

    print(f"   ✓ Stream started (correlation_id: {correlation_id})")

    # Receive chunks for ~1.5 seconds, then cancel
    received_chunks = 0
    cancellation_sent_time = None
    cancellation_received_time = None

    try:
        while True:
            # Wait for next chunk (with timeout)
            message = await supervisor.next_stream_event(correlation_id, timeout=1.0)
            msg_signal = message.get("signal")
            payload = message.get("payload", {})

            if msg_signal == signals.STREAM_CHUNK:
                chunk_num = payload.get("chunk_number", received_chunks)
                print(f"   [Stream] Received chunk #{chunk_num}")
                received_chunks += 1

                # Cancel after 5 chunks (~1.5 seconds)
                if received_chunks == 5:
                    print(
                        f"\n4. Sending cancellation command (after {received_chunks} chunks)..."
                    )
                    cancellation_sent_time = time.time()

                    # Send cancel command
                    cancel_message = create_message(
                        signal=signals.CANCEL_STREAM,
                        payload={"worker_id": worker_id},
                        correlation_id=correlation_id,
                        worker_id=worker_id,
                    )
                    await supervisor.send_raw(cancel_message)
                    print(f"   ✓ Cancellation sent at {cancellation_sent_time:.3f}")

            elif msg_signal == signals.STREAM_CANCELLED:
                cancellation_received_time = time.time()
                print("\n5. Cancellation acknowledged!")
                print(f"   Status: {payload.get('status')}")
                break

            elif msg_signal == signals.STREAM_END:
                print("\n5. Stream completed normally")
                print(f"   Total chunks: {payload.get('total_chunks')}")
                break

            elif msg_signal == signals.STREAM_ERROR:
                print(f"\n5. Stream error: {payload.get('error')}")
                break

    except TimeoutError:
        print("\n   [Timeout] Stream operation timed out")
    except Exception as e:
        print(f"\n   [Error] An error occurred: {e}")
        import traceback

        traceback.print_exc()

    # Calculate and display cancellation latency
    if cancellation_sent_time and cancellation_received_time:
        latency_ms = (cancellation_received_time - cancellation_sent_time) * 1000
        print(f"\n{'=' * 80}")
        print("CANCELLATION LATENCY MEASUREMENT")
        print(f"{'=' * 80}")
        print(f"  Cancellation sent:     {cancellation_sent_time:.3f}s")
        print(f"  Cancellation received: {cancellation_received_time:.3f}s")
        print(f"  Latency:               {latency_ms:.1f}ms")
        print()
        if latency_ms < 150:
            print(f"  ✓ EXCELLENT: Cancellation processed in {latency_ms:.1f}ms")
            print("    (v4.0.0's automatic yielding ensures <100ms response)")
        elif latency_ms < 500:
            print(f"  ✓ GOOD: Cancellation processed in {latency_ms:.1f}ms")
        else:
            print(f"  ⚠ SLOW: Cancellation took {latency_ms:.1f}ms")
            print("    (Expected <100ms with v4.0.0)")
        print(f"{'=' * 80}\n")

    print(f"Total chunks received: {received_chunks}")
    print("Expected if cancelled: ~5 chunks")
    print("Expected if completed: 30 chunks")

    print("\n6. Shutting down worker and supervisor...")
    await supervisor.stop(worker_id)
    await supervisor.shutdown()
    print("   ✓ Shutdown complete")

    print("\n" + "=" * 80)
    print("Demo complete!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(async_streaming_demo())
