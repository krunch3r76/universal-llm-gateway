"""
Main application controller.
"""

import threading

from universal_logging import get_logger

from ..model import MemoryBackend, SessionManager, TransportClient
from ..view import MainWindow
from .session_controller import SessionController

logger = get_logger(__name__)


class AppController:
    """
    Main application controller.

    Coordinates:
    - Model initialization and management
    - View creation and updates
    - Transport connection and events
    - Session management
    """

    def __init__(self):
        """Initialize application controller."""
        # Initialize model components
        self.memory_backend = MemoryBackend()
        self.transport_client = TransportClient(self.memory_backend)
        self.session_manager = SessionManager(self.memory_backend)

        # View components (set after initialization)
        self.view: MainWindow | None = None
        self.session_controller: SessionController | None = None

        # Set up observers
        self._setup_observers()

    def start(self) -> None:
        """Initialize and start all components."""
        try:
            # Create and configure view
            self.view = MainWindow()
            self.connect_view(self.view)

            # Start transport client in background thread
            transport_thread = threading.Thread(
                target=self._start_transport, daemon=True
            )
            transport_thread.start()

            # Start GUI main loop
            self.view.run()

        except Exception as e:
            logger.error(f"Error starting application: {e}")
            raise

    def connect_view(self, view: MainWindow) -> None:
        """
        Connect view components to controller.

        Args:
            view: MainWindow instance
        """
        try:
            self.view = view

            # Create session controller
            self.session_controller = SessionController(
                self.session_manager, self.view.session_view
            )

            # Set up callbacks
            self.view.set_session_selection_callback(
                self.session_controller.handle_session_changed
            )

            # Set transport status callback
            self.transport_client.set_status_callback(self.update_connection_status)

        except Exception as e:
            logger.error(f"Error connecting view: {e}")

    def _setup_observers(self) -> None:
        """Set up model observers."""
        try:
            # Observe memory backend for data changes
            self.memory_backend.add_observer(self._handle_data_changed)

            # Observe session manager for selection changes
            self.session_manager.add_observer(self._handle_session_changed)

        except Exception as e:
            logger.error(f"Error setting up observers: {e}")

    def _start_transport(self) -> None:
        """Start transport client connection."""
        try:
            if not self.transport_client.connect():
                self.update_connection_status(
                    "error", "Failed to connect to Universal Stargate"
                )
        except Exception as e:
            logger.error(f"Error starting transport: {e}")
            self.update_connection_status("error", str(e))

    def _handle_data_changed(self) -> None:
        """Handle memory backend data changes."""
        try:
            if self.view and self.view.is_running():
                # Update session list
                sessions = self.session_manager.get_all_sessions()
                self.view.session_list.update_sessions(sessions)

                # Update current session view if needed
                if self.session_controller:
                    self.session_controller.update_view()

        except Exception as e:
            logger.error(f"Error handling data change: {e}")

    def _handle_session_changed(self, session_id: str) -> None:
        """
        Handle session selection changes.

        Args:
            session_id: ID of newly selected session
        """
        try:
            if self.view and self.view.is_running():
                # Update session list selection
                self.view.session_list.select_session(session_id)

                # Update session view
                if self.session_controller:
                    self.session_controller.update_view()

        except Exception as e:
            logger.error(f"Error handling session change: {e}")

    def update_connection_status(self, status: str, message: str) -> None:
        """
        Update connection status in view.

        Args:
            status: Status type (connected, disconnected, error)
            message: Status message
        """
        try:
            if self.view and self.view.is_running():
                level = "info"
                if status == "error":
                    level = "error"
                elif status == "disconnected":
                    level = "warning"

                self.view.update_status(message, level)

        except Exception as e:
            logger.error(f"Error updating connection status: {e}")

    def stop(self) -> None:
        """Stop application and clean up."""
        try:
            # Disconnect transport
            if self.transport_client:
                self.transport_client.disconnect()

            # Close view
            if self.view:
                self.view.root.quit()
                self.view.root.destroy()

        except Exception as e:
            logger.error(f"Error stopping application: {e}")
            # Force quit if error
            if self.view:
                try:
                    self.view.root.quit()
                    self.view.root.destroy()
                except:
                    pass
