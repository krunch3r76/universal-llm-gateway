"""
Main window for the universal_stargate GUI.

Provides the primary GUI window and coordinates the overall view layout.
"""

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from universal_logging import get_logger

from ..model.data_structures import DisplayData
from .three_panel_view import ThreePanelView

logger = get_logger(__name__)


class MainWindow:
    """Main GUI window - pure view component"""

    def __init__(self):
        """Initialize main window"""
        self.root = tk.Tk()
        self.three_panel_view = None
        self.setup_window()
        self.setup_layout()

    def setup_window(self):
        """Configure main window"""
        self.root.title("Universal Stargate Monitor")
        self.root.geometry("1400x800")
        self.root.minsize(1000, 600)

        # Configure grid
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        # Set window icon if available (optional)
        try:
            # You can add an icon here if you have one
            # self.root.iconbitmap("icon.ico")
            pass
        except:
            pass

        # Handle window close event
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def setup_layout(self):
        """Setup the main layout"""
        # Create main container
        main_container = ttk.Frame(self.root)
        main_container.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # Configure grid weights
        main_container.columnconfigure(0, weight=1)
        main_container.rowconfigure(0, weight=1)
        main_container.rowconfigure(1, weight=0)  # Status bar

        # Create three panel view
        self.three_panel_view = ThreePanelView(main_container)

        # Create status bar
        self.status_bar = self.create_status_bar(main_container)
        self.status_bar.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(5, 0))

    def create_status_bar(self, parent):
        """
        Create status bar at bottom of window.

        Args:
            parent: Parent widget for status bar

        Returns:
            Status bar frame
        """
        status_frame = ttk.Frame(parent)

        # Create status variables
        self.status_var = tk.StringVar(value="Status: Ready")
        self.connection_var = tk.StringVar(value="●")
        self.history_var = tk.StringVar(value="")  # Shows "3/50" or "LIVE"

        # Create status labels
        self.status_label = ttk.Label(status_frame, textvariable=self.status_var)
        self.status_label.pack(side=tk.LEFT)

        # Connection indicator (right side)
        self.connection_label = ttk.Label(
            status_frame, textvariable=self.connection_var, foreground="gray"
        )
        self.connection_label.pack(side=tk.RIGHT)

        connection_text = ttk.Label(status_frame, text="Connection: ")
        connection_text.pack(side=tk.RIGHT)

        # History navigation controls (center-right)
        nav_frame = ttk.Frame(status_frame)
        nav_frame.pack(side=tk.RIGHT, padx=20)

        self.back_btn = ttk.Button(
            nav_frame, text="◀", width=3, command=self._on_navigate_back
        )
        self.back_btn.pack(side=tk.LEFT)

        self.history_label = ttk.Label(
            nav_frame, textvariable=self.history_var, width=12, anchor=tk.CENTER
        )
        self.history_label.pack(side=tk.LEFT, padx=5)

        self.forward_btn = ttk.Button(
            nav_frame, text="▶", width=3, command=self._on_navigate_forward
        )
        self.forward_btn.pack(side=tk.LEFT)

        self.live_btn = ttk.Button(
            nav_frame, text="⏺ LIVE", width=8, command=self._on_go_live
        )
        self.live_btn.pack(side=tk.LEFT, padx=(10, 0))

        # Keyboard shortcuts
        self.root.bind("<Left>", lambda e: self._on_navigate_back())
        self.root.bind("<Right>", lambda e: self._on_navigate_forward())
        self.root.bind("<Home>", lambda e: self._on_go_live())

        # Initialize navigation callbacks (will be set by StargateGUI)
        self._nav_back_callback: Callable[[], None] | None = None
        self._nav_forward_callback: Callable[[], None] | None = None
        self._go_live_callback: Callable[[], None] | None = None

        # Initial state: disable navigation until events arrive
        self._update_nav_buttons(0, 0, True, False)

        return status_frame

    def update_display(self, display_data: DisplayData):
        """
        Update display with new data.

        Args:
            display_data: DisplayData object to display
        """
        try:
            if self.three_panel_view:
                self.three_panel_view.update(display_data)

                # Update status
                event_id = display_data.event_info.get("id", "unknown")
                timestamp = display_data.event_info.get("timestamp", "unknown")
                self.update_status(f"Received event {event_id} at {timestamp}")

        except Exception as e:
            logger.error(f"Error updating main window display: {e}")
            self.show_error(f"Display error: {e}")

    def update_status(self, message: str):
        """
        Update status message.

        Args:
            message: Status message to display
        """
        self.status_var.set(f"Status: {message}")

    def update_connection_status(self, connected: bool):
        """
        Update connection status indicator.

        Args:
            connected: True if connected, False otherwise
        """
        if connected:
            self.connection_var.set("●")
            self.connection_label.configure(foreground="green")
        else:
            self.connection_var.set("●")
            self.connection_label.configure(foreground="red")

    def show_error(self, error_message: str):
        """
        Show error message to user.

        Args:
            error_message: Error message to display
        """
        logger.error(f"GUI Error: {error_message}")
        self.update_status(f"Error: {error_message}")

        # Show error in panels if severe
        if self.three_panel_view:
            self.three_panel_view._show_error(error_message)

    def show_info(self, info_message: str):
        """
        Show info message to user.

        Args:
            info_message: Info message to display
        """
        self.update_status(info_message)

    def on_closing(self):
        """Handle window closing event"""
        try:
            # Ask for confirmation (optional)
            # result = messagebox.askquestion("Exit", "Are you sure you want to exit?")
            # if result == 'yes':
            #     self.root.quit()
            #     self.root.destroy()

            # For now, just close without confirmation
            self.root.quit()
            self.root.destroy()

        except Exception as e:
            logger.error(f"Error during window close: {e}")
            # Force close if error
            self.root.quit()
            self.root.destroy()

    def run(self):
        """Start GUI main loop"""
        try:
            logger.debug("Starting GUI main loop")
            self.root.mainloop()
        except Exception as e:
            logger.error(f"Error in GUI main loop: {e}")
            raise

    def close(self):
        """Close window programmatically"""
        try:
            self.root.quit()
            self.root.destroy()
        except Exception as e:
            logger.error(f"Error closing window: {e}")

    def clear_display(self):
        """Clear all display panels"""
        if self.three_panel_view:
            self.three_panel_view.clear()
        self.update_status("Display cleared")

    def is_running(self) -> bool:
        """
        Check if window is still running.

        Returns:
            True if window exists and is running
        """
        try:
            return bool(self.root and self.root.winfo_exists())
        except:
            return False

    def set_navigation_callbacks(
        self,
        back: Callable[[], None],
        forward: Callable[[], None],
        go_live: Callable[[], None],
    ) -> None:
        """
        Set navigation callbacks.

        Args:
            back: Called when back button clicked
            forward: Called when forward button clicked
            go_live: Called when live button clicked
        """
        self._nav_back_callback = back
        self._nav_forward_callback = forward
        self._go_live_callback = go_live

    def _on_navigate_back(self) -> None:
        """Handle back button click."""
        if self._nav_back_callback:
            self._nav_back_callback()

    def _on_navigate_forward(self) -> None:
        """Handle forward button click."""
        if self._nav_forward_callback:
            self._nav_forward_callback()

    def _on_go_live(self) -> None:
        """Handle live button click."""
        if self._go_live_callback:
            self._go_live_callback()

    def update_navigation_state(
        self,
        current_index: int,
        total_count: int,
        is_live: bool,
        has_new_requests: bool,
    ) -> None:
        """
        Update navigation UI state.

        Args:
            current_index: 1-based current position (0 when live)
            total_count: Total events in history
            is_live: True if in live mode
            has_new_requests: True if new requests arrived while browsing
        """
        self._update_nav_buttons(current_index, total_count, is_live, has_new_requests)

    def _update_nav_buttons(
        self,
        current_index: int,
        total_count: int,
        is_live: bool,
        has_new_requests: bool,
    ) -> None:
        """Update button states and history label."""
        if is_live:
            self.history_var.set(f"LIVE ({total_count})")
            self.history_label.configure(foreground="green")
            # Can go back if there's history
            self.back_btn.configure(state=tk.NORMAL if total_count > 0 else tk.DISABLED)
            self.forward_btn.configure(state=tk.DISABLED)
            # In LIVE mode, button is disabled (already live)
            self.live_btn.configure(state=tk.DISABLED)
        else:
            self.history_var.set(f"{current_index}/{total_count}")
            # Highlight if new requests available
            if has_new_requests:
                self.history_label.configure(foreground="red")
                self.live_btn.configure(state=tk.NORMAL, text="LIVE ●")  # Indicator
            else:
                self.history_label.configure(foreground="orange")
                self.live_btn.configure(state=tk.NORMAL, text="LIVE")
            # Can go back if not at oldest
            self.back_btn.configure(
                state=tk.NORMAL if current_index > 1 else tk.DISABLED
            )
            # Can always go forward (toward live)
            self.forward_btn.configure(state=tk.NORMAL)
