"""Test resource cleanup to detect FD leaks.

Verifies that RPC clients properly clean up file descriptors after use.
"""

import asyncio

import pytest

from universal_protocol import AsyncRPCClient
from universal_protocol.observability import get_debug_stats


@pytest.mark.asyncio
async def test_resource_cleanup():
    """Test that RPC clients don't leak file descriptors."""
    # Get initial FD count
    initial_stats = get_debug_stats()
    initial_fds = initial_stats.get("fds_open", -1)

    # Skip test if FD counting not available
    if initial_fds == -1:
        pytest.skip("FD counting not available on this platform")

    # Create RPC client and make multiple calls
    socket_path = "/tmp/test-resource-cleanup.sock"

    # Note: In a real test, we'd need a running server on this socket
    # For now, we're testing the client-side cleanup pattern

    try:
        client = AsyncRPCClient(socket_path)

        # Make 100 health calls (would fail without server, but tests cleanup)
        for i in range(100):
            try:
                await client.call("health", {})
            except Exception:
                # Expected to fail without server
                pass

        # Close client
        await client.close()

    except Exception:
        # Expected if no server is running
        pass

    # Give OS time to clean up
    await asyncio.sleep(0.1)

    # Get final FD count
    final_stats = get_debug_stats()
    final_fds = final_stats.get("fds_open", -1)

    # Assert no FD leak (allow small delta for OS variations)
    fd_delta = final_fds - initial_fds
    assert abs(fd_delta) <= 2, (
        f"FD leak detected: initial={initial_fds}, final={final_fds}, delta={fd_delta}"
    )


@pytest.mark.asyncio
async def test_multiple_client_cleanup():
    """Test cleanup with multiple client instances."""
    # Get initial FD count
    initial_stats = get_debug_stats()
    initial_fds = initial_stats.get("fds_open", -1)

    # Skip test if FD counting not available
    if initial_fds == -1:
        pytest.skip("FD counting not available on this platform")

    # Create multiple clients
    clients = []
    for i in range(10):
        try:
            client = AsyncRPCClient(f"/tmp/test-client-{i}.sock")
            clients.append(client)
            # Try to make a call
            try:
                await client.call("health", {})
            except Exception:
                pass
        except Exception:
            pass

    # Close all clients
    for client in clients:
        try:
            await client.close()
        except Exception:
            pass

    # Give OS time to clean up
    await asyncio.sleep(0.1)

    # Get final FD count
    final_stats = get_debug_stats()
    final_fds = final_stats.get("fds_open", -1)

    # Assert no FD leak (allow small delta for OS variations)
    fd_delta = final_fds - initial_fds
    assert abs(fd_delta) <= 2, (
        f"FD leak detected: initial={initial_fds}, final={final_fds}, delta={fd_delta}"
    )
