"""
Main application window and layout management.
"""

import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk

from universal_logging import get_logger

from .session_list import SessionListView
from .session_view import SessionDetailView

logger = get_logger(__name__)


class MainWindow:
    """
    Main GUI window - pure view component.

    Features:
    - Split panel layout
    - Status bar
    - Menu bar
    - Window management
    """

    def __init__(self):
        """Initialize main window."""
        self.root = tk.Tk()
        self.session_list = None
        self.session_view = None
        self.status_bar = None
        self.status_var = None
        self.setup_window()
        self.setup_menu()
        self.setup_layout()

    def setup_window(self):
        """Configure main window properties."""
        # Set window title and size
        self.root.title("Universal Stargate Monitor")
        self.root.geometry("1400x800")
        self.root.minsize(1000, 600)

        # Configure grid
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        # Set window icon if available
        try:
            # self.root.iconbitmap("icon.ico")  # Add icon if available
            pass
        except Exception:
            pass

        # Handle window close
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def setup_menu(self):
        """Create menu bar with options."""
        # Create menu bar
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)

        # File menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Exit", command=self.on_closing)

        # View menu
        view_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=view_menu)
        view_menu.add_command(label="Clear Display", command=self.clear_display)

        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About", command=self.show_about)

    def setup_layout(self):
        """Create main layout with session list and detail view."""
        # Create main container
        main_container = ttk.Frame(self.root)
        main_container.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # Configure grid weights
        main_container.columnconfigure(0, weight=0)  # Session list (fixed width)
        main_container.columnconfigure(1, weight=1)  # Session view (stretches)
        main_container.columnconfigure(2, weight=0)  # Sash (grip)
        main_container.rowconfigure(0, weight=1)
        main_container.rowconfigure(1, weight=0)  # Status bar

        # Create session list panel
        session_list_frame = ttk.Frame(main_container, width=300)
        session_list_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        session_list_frame.grid_propagate(False)  # Maintain width

        self.session_list = SessionListView(session_list_frame)
        self.session_list.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # Add vertical separator
        separator = ttk.Separator(main_container, orient=tk.VERTICAL)
        separator.grid(row=0, column=1, sticky=(tk.N, tk.S), padx=2)

        # Create session detail panel
        self.session_view = SessionDetailView(main_container)
        self.session_view.grid(row=0, column=2, sticky=(tk.W, tk.E, tk.N, tk.S))

        # Create status bar
        self.setup_status_bar(main_container)

    def setup_status_bar(self, parent):
        """
        Create status bar.

        Args:
            parent: Parent widget
        """
        status_frame = ttk.Frame(parent)
        status_frame.grid(row=1, column=0, columnspan=3, sticky=(tk.W, tk.E))

        # Add separator above status bar
        separator = ttk.Separator(status_frame, orient=tk.HORIZONTAL)
        separator.grid(row=0, column=0, sticky=(tk.W, tk.E))

        # Create status label
        self.status_var = tk.StringVar()
        self.status_bar = ttk.Label(
            status_frame, textvariable=self.status_var, anchor=tk.W, padding=(5, 2)
        )
        self.status_bar.grid(row=1, column=0, sticky=(tk.W, tk.E))

        # Set initial status
        self.update_status("Ready")

    def set_session_selection_callback(self, callback: Callable[[str], None]) -> None:
        """
        Set callback for session selection.

        Args:
            callback: Function taking session ID parameter
        """
        if self.session_list:
            self.session_list.set_selection_callback(callback)

    def update_status(self, status: str, level: str = "info") -> None:
        """
        Update status bar with message.

        Args:
            status: Status message
            level: Message level (info, warning, error)
        """
        try:
            # Configure status appearance based on level
            if level == "error":
                self.status_bar.configure(foreground="red")
            elif level == "warning":
                self.status_bar.configure(foreground="orange")
            else:
                self.status_bar.configure(foreground="black")

            self.status_var.set(status)

        except Exception as e:
            logger.error(f"Error updating status: {e}")

    def clear_display(self) -> None:
        """Clear all display panels."""
        try:
            if self.session_view:
                self.session_view.clear()
            self.update_status("Display cleared")
        except Exception as e:
            logger.error(f"Error clearing display: {e}")

    def show_about(self) -> None:
        """Show about dialog."""
        messagebox.showinfo(
            "About Universal Stargate Monitor",
            "Universal Stargate Monitor v2.0\n\n"
            "A monitoring interface for Universal Stargate chat completions.",
        )

    def on_closing(self) -> None:
        """Handle window closing event."""
        try:
            result = messagebox.askquestion(
                "Exit", "Are you sure you want to exit?", icon="warning"
            )
            if result == "yes":
                self.root.quit()
                self.root.destroy()
        except Exception as e:
            logger.error(f"Error during window close: {e}")
            self.root.quit()
            self.root.destroy()

    def run(self) -> None:
        """Start GUI main loop."""
        try:
            logger.debug("Starting GUI main loop")
            self.root.mainloop()
        except Exception as e:
            logger.error(f"Error in GUI main loop: {e}")
            raise

    def is_running(self) -> bool:
        """
        Check if window is still running.

        Returns:
            True if window exists and is running
        """
        try:
            return bool(self.root and self.root.winfo_exists())
        except Exception:
            return False
