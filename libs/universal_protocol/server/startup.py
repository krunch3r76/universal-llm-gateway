"""Uvicorn server initialization and startup helper for Universal Protocol.

Provides a simple serve() function that starts the ASGI server on a Unix socket
with uvloop event loop for performance.

Usage:
    from universal_protocol.server import serve

    serve(
        app=app,
        socket_path="/tmp/universal-protocol/worker-1.sock",
        loop="uvloop",
        workers=1
    )
"""

import socket

from universal_logging import get_logger

from .uds_security import bind_socket

logger = get_logger(__name__)


async def serve(
    app,
    socket_path: str,
    host: str = "unix",
    port: int = 0,
    loop: str = "uvloop",
    workers: int = 1,
    log_level: str = "info",
    pre_bind_socket: bool = True,
) -> None:
    """Start ASGI server on Unix socket.

    This is a helper function that configures and starts uvicorn with
    appropriate settings for the MVP. By default, it pre-binds the socket
    with security restrictions before passing it to uvicorn.

    Args:
        app: Starlette ASGI application instance
        socket_path: Unix socket path (e.g., /tmp/universal-protocol/worker-1.sock)
        host: Server host type ("unix" for Unix sockets, default: "unix")
        port: Port number (unused for Unix sockets, default: 0)
        loop: Event loop backend ("uvloop" or "asyncio", default: "uvloop")
        workers: Number of worker processes (default: 1, always 1 for MVP)
        log_level: Logging level (default: "info")
        pre_bind_socket: Pre-bind socket with security restrictions (default: True)

    Raises:
        ImportError: If uvicorn is not installed
        OSError: If socket binding fails

    Example:
        >>> import asyncio
        >>> from universal_protocol.server import app, serve
        >>> asyncio.run(serve(
        ...     app=app,
        ...     socket_path="/tmp/universal-protocol/worker-1.sock"
        ... ))
    """
    # Import here to avoid hard dependency at module load time
    try:
        import uvicorn
    except ImportError as e:
        raise ImportError("uvicorn is required for server startup") from e

    import atexit
    import os
    from pathlib import Path

    # Ensure socket directory exists
    socket_dir = Path(socket_path).parent
    socket_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Starting ASGI server on Unix socket: {socket_path}")
    logger.info(f"Event loop: {loop}")

    bound_socket: socket.socket | None = None

    if pre_bind_socket:
        # Pre-bind socket with security restrictions
        try:
            bound_socket = bind_socket(
                socket_path=socket_path, permissions=0o600, unlink_first=True
            )
            logger.info(f"Pre-bound socket with security restrictions: {socket_path}")

            # Register cleanup handler
            def cleanup_socket():
                if bound_socket:
                    try:
                        bound_socket.close()
                    except:
                        pass
                if os.path.exists(socket_path):
                    try:
                        os.unlink(socket_path)
                    except:
                        pass

            atexit.register(cleanup_socket)

        except OSError as e:
            logger.error(f"Failed to pre-bind socket: {e}")
            raise

    # Configure uvicorn
    if bound_socket:
        # Use pre-bound socket
        config = uvicorn.Config(
            app=app,
            loop=loop,
            workers=workers,
            log_level=log_level,
        )
        server = uvicorn.Server(config)

        # Run server with pre-bound socket
        await server.serve([bound_socket])

    else:
        # Let uvicorn handle socket creation (fallback)
        config = uvicorn.Config(
            app=app,
            uds=socket_path,
            loop=loop,
            workers=workers,
            log_level=log_level,
        )

        server = uvicorn.Server(config)

        # Monkey-patch to fix permissions after binding

        original_bind = server.startup

        async def startup_with_permissions(sockets=None):
            # Let uvicorn create the socket normally
            await original_bind(sockets)

            # Then immediately fix permissions
            if os.path.exists(socket_path):
                try:
                    os.chmod(socket_path, 0o600)
                    logger.info(f"Set socket permissions to 0600: {socket_path}")
                except OSError as e:
                    logger.error(f"Failed to set socket permissions: {e}")

        server.startup = startup_with_permissions

        # Run server with permission fix
        await server.serve()

    # Post-startup verification (regardless of bind method)
    if os.path.exists(socket_path):
        try:
            stat_info = os.stat(socket_path)
            current_perms = stat_info.st_mode & 0o777
            if current_perms != 0o600:
                logger.warning(
                    f"Socket permissions are {oct(current_perms)}, expected 0o600"
                )
        except OSError as e:
            logger.error(f"Failed to verify socket permissions: {e}")
