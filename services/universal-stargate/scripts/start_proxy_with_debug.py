#!/usr/bin/env python3
"""
Integration Example: How to add debug event broadcasting to Universal Stargate

This script demonstrates the correct way to integrate debug event broadcasting
with the Universal Stargate proxy using the updated architecture.
"""

import asyncio
import os
import sys
import time
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from universal_event_bus import EventBus, MinimalEventDebugBroadcaster
from universal_logging import get_logger

logger = get_logger(__name__)


async def main():
    """Demonstrate correct integration of debug event broadcasting."""
    logger.info("🚀 Universal Stargate with Debug Event Broadcasting")

    # Step 1: Start debug broadcaster
    debug_broadcaster = MinimalEventDebugBroadcaster("/tmp/stargate_debug_events.sock")
    await debug_broadcaster.start_debug_server()

    # Step 2: Create EventBus with debug broadcasting enabled
    event_bus = EventBus(debug_broadcaster)

    logger.info("🔍 Debug Events Server started")
    logger.info("📡 Debug socket: /tmp/stargate_debug_events.sock")
    logger.info("🔍 Connect debug clients to monitor events")

    # Step 3: Initialize your components normally
    # Health monitoring is handled via WebSocket in MultiGatewayManager
    # scheduler = RequestScheduler(event_bus)
    # queue = EnhancedRequestQueue(event_bus, ...)
    # etc.

    # For demonstration, let's create some test events
    from src.scheduling.events import (
        GatewayStateChanged,
        RequestQueued,
        SystemStarted,
    )

    # Publish some test events - they will be automatically broadcasted to debug clients
    await event_bus.publish(
        SystemStarted(pid=os.getpid(), role="master", started_at=time.time())
    )
    await event_bus.publish(
        GatewayStateChanged(
            url="http://localhost:8000",
            connectivity="reachable",
            health="healthy",
            previous_connectivity="unreachable",
            previous_health="unknown",
            transition_type="both",
            check_duration_ms=25,
        )
    )
    await event_bus.publish(
        RequestQueued(request_id="test-123", model_id="test-model", priority=1)
    )

    logger.info("📡 Test events published - check debug clients")
    logger.info("💡 Usage: python scripts/monitor_events.py sniff")

    # Check debug status
    status = event_bus.get_debug_status()
    logger.info(f"🔍 Debug status: {status}")

    try:
        # Keep running
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("🛑 Shutting down...")
        await debug_broadcaster.stop_debug_server()


if __name__ == "__main__":
    asyncio.run(main())
