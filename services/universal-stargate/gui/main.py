"""
Main application class for the universal_stargate GUI.

Coordinates the MVC components and provides the primary application interface.
"""

# MONKEY PATCH: Configure JSON to use unicode by default
import json
import sys

from .controller.event_controller import EventController
from .model.network_receiver import NetworkReceiver
from .view.main_window import MainWindow

_original_dumps = json.dumps


def unicode_friendly_dumps(obj, **kwargs):
    """JSON dumps with ensure_ascii=False by default."""
    if "ensure_ascii" not in kwargs:
        kwargs["ensure_ascii"] = False
    return _original_dumps(obj, **kwargs)


json.dumps = unicode_friendly_dumps

# Configure logging with universal_logging
from universal_logging import get_logger

# Auto-initialization: logger will be initialized on first use
logger = None


class StargateGUI:
    """Main application class - coordinates MVC components"""

    def __init__(
        self,
        transport: str = "unix",
        unix_socket: str = "/tmp/stargate_events.sock",
        transport_config: dict = None,
    ):
        """
        Initialize the universal_stargate GUI application.

        Args:
            transport: Transport type ('unix' or 'tcp')
            unix_socket: Unix socket path for Unix transport
            transport_config: Complete transport configuration dict
        """
        self.transport = transport
        self.unix_socket = unix_socket
        self.transport_config = transport_config or {}

        # Extract transport info from config if provided
        if self.transport_config:
            self.transport = self.transport_config.get("transport", transport)
        self.network_receiver = None
        self.controller = None
        self.view = None
        self.running = False

    def start(self):
        """Start the application"""
        try:
            # Setup logging if not already done
            global logger
            if logger is None:
                logger = get_logger("gui")  # GUI-specific logger

            logger.info("Starting Universal Stargate GUI")
            logger.info(f"  Transport: {self.transport}")
            if self.transport == "unix":
                logger.info(f"  Unix socket: {self.unix_socket}")
            elif self.transport == "tcp":
                logger.info(
                    f"  TCP: {self.transport_config.get('host')}:{self.transport_config.get('port')}"
                )
            logger.info("Universal Stargate GUI startup complete")

            # Create view (main window)
            self.view = MainWindow()
            logger.info("Created main window")

            # Create controller with view callback
            self.controller = EventController(self.view.update_display)
            logger.info("Created event controller")

            # Wire navigation callbacks (wrap bool-returning methods)
            self.view.set_navigation_callbacks(
                back=lambda: self.controller.navigate_back(),
                forward=lambda: self.controller.navigate_forward(),
                go_live=self.controller.go_live,
            )

            # Set controller's navigation state callback to update view
            self.controller.set_navigation_callback(self.view.update_navigation_state)
            logger.info("Navigation controls initialized")

            # Create and start network receiver
            # Use provided transport_config or create default config
            if self.transport_config:
                receiver_config = self.transport_config.copy()
            else:
                receiver_config = {
                    "transport": self.transport,
                    "unix_socket_path": self.unix_socket,
                    "use_universal_transport": True,  # Use new universal_transport system
                }

            self.network_receiver = NetworkReceiver(
                callback=self.controller.process_event,
                root_window=self.view.root,
                config=receiver_config,
            )
            self.network_receiver.start()

            # Log connection info
            if self.transport == "unix":
                logger.info(f"Started Unix socket receiver on {self.unix_socket}")
                self.view.show_info(f"Connected via Unix socket: {self.unix_socket}")
            elif self.transport == "tcp":
                tcp_host = receiver_config.get("host", "localhost")
                tcp_port = receiver_config.get("port", 9997)
                logger.info(f"Started TCP receiver on {tcp_host}:{tcp_port}")
                self.view.show_info(f"Connected via TCP: {tcp_host}:{tcp_port}")

            # Update connection status
            self.view.update_connection_status(True)

            # Set up window close handler
            self.view.root.protocol("WM_DELETE_WINDOW", self.close)

            self.running = True

            # Start GUI main loop
            logger.info("Starting GUI main loop...")
            self.view.run()

        except Exception as e:
            if logger:
                logger.error(f"Failed to start GUI: {e}")
            self.show_startup_error(str(e))
            self.close()

    def close(self):
        """Clean shutdown of all components"""
        try:
            if logger:
                logger.info("Shutting down Universal Stargate GUI")
            self.running = False

            # Stop network receiver
            if self.network_receiver:
                self.network_receiver.stop()
                if logger:
                    logger.info("Stopped network receiver")

            # Close view
            if self.view:
                self.view.close()
                if logger:
                    logger.info("Closed main window")

            if logger:
                logger.info("Universal Stargate GUI shutdown complete")

        except Exception as e:
            if logger:
                logger.error(f"Error during shutdown: {e}")

    def show_startup_error(self, error_message: str):
        """
        Show startup error to user.

        Args:
            error_message: Error message to display
        """
        try:
            # Try to show error in GUI if possible
            if self.view:
                self.view.show_error(f"Startup error: {error_message}")
            else:
                # Fall back to console
                print(f"❌ Startup Error: {error_message}")

        except Exception as e:
            print(f"❌ Startup Error: {error_message}")
            print(f"❌ Additional error showing startup error: {e}")

    def get_controller(self):
        """
        Get the event controller for extension registration.

        Returns:
            EventController instance or None
        """
        return self.controller

    def get_stats(self) -> dict:
        """
        Get application statistics.

        Returns:
            Dictionary with application stats
        """
        stats = {
            "running": self.running,
            "transport": self.transport,
            "unix_socket": self.unix_socket,
            "network_receiver_active": self.network_receiver
            and self.network_receiver.running
            if self.network_receiver
            else False,
            "view_active": self.view and self.view.is_running() if self.view else False,
        }

        if self.controller:
            stats["controller"] = self.controller.get_stats()

        return stats


def main():
    """Main entry point for command line usage"""
    import argparse

    parser = argparse.ArgumentParser(description="Universal Stargate GUI Monitor")
    parser.add_argument(
        "--transport",
        type=str,
        choices=["unix", "tcp"],
        default="unix",
        help="Transport type: unix or tcp (default: unix)",
    )
    parser.add_argument(
        "--unix-socket",
        type=str,
        default="/tmp/stargate_events.sock",
        help="Unix socket path (default: /tmp/stargate_events.sock)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="localhost",
        help="TCP host for remote monitoring (default: localhost)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9997,
        help="TCP port for remote monitoring (default: 9997)",
    )
    parser.add_argument(
        "--use-universal-transport",
        action="store_true",
        default=True,
        help="Use universal_transport AsyncMonitoringClient (default: True)",
    )
    parser.add_argument(
        "--no-universal-transport",
        action="store_true",
        help="Disable universal_transport and use legacy socket implementation",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    # Set log level based on --debug flag

    # Setup universal_logging with proper configuration
    # Note: log_file path will be handled by load_logging_config() using DATA_DIR
    from config.logging_config import load_logging_config

    load_logging_config()

    # Get GUI-specific logger
    global logger
    logger = get_logger("gui")

    # Create transport config
    transport_config = {
        "transport": args.transport,
        "unix_socket_path": args.unix_socket,
        "host": args.host,
        "port": args.port,
        "use_universal_transport": not args.no_universal_transport,
    }

    # Create and start the application
    app = StargateGUI(
        transport=args.transport,
        unix_socket=args.unix_socket,
        transport_config=transport_config,
    )

    try:
        app.start()
    except KeyboardInterrupt:
        if logger:
            logger.info("Received keyboard interrupt")
        app.close()
    except Exception as e:
        if logger:
            logger.error(f"Unhandled error: {e}")
        app.close()
        sys.exit(1)


if __name__ == "__main__":
    main()
