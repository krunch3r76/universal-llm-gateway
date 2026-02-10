"""
Session list navigation panel.
"""

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from universal_logging import get_logger

logger = get_logger(__name__)


class SessionListView:
    """
    Scrollable list of sessions with metadata.

    Features:
    - Session list with status indicators
    - Selection handling
    - Auto-scrolling for new sessions
    - Status filtering
    """

    def __init__(self, parent):
        """
        Initialize session list view.

        Args:
            parent: Parent tkinter widget
        """
        self.parent = parent
        self.frame = ttk.Frame(parent)
        self.session_listbox = None
        self.sessions: list[tuple[str, str, str]] = []  # (id, summary, status)
        self.selection_callback = None
        self.filter_var = None
        self.setup_ui()

    def setup_ui(self) -> None:
        """Create scrollable listbox with session entries."""
        # Configure frame
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)

        # Create filter frame
        filter_frame = ttk.Frame(self.frame)
        filter_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), padx=5, pady=5)

        # Add filter label
        filter_label = ttk.Label(filter_frame, text="Show:", font=("TkDefaultFont", 10))
        filter_label.pack(side=tk.LEFT, padx=(0, 5))

        # Add filter radiobuttons
        self.filter_var = tk.StringVar(value="all")
        filters = [
            ("All", "all"),
            ("Active", "active"),
            ("Complete", "complete"),
            ("Error", "error"),
        ]

        for text, value in filters:
            rb = ttk.Radiobutton(
                filter_frame,
                text=text,
                value=value,
                variable=self.filter_var,
                command=self._apply_filter,
            )
            rb.pack(side=tk.LEFT, padx=5)

        # Create listbox with scrollbar
        listbox_frame = ttk.Frame(self.frame)
        listbox_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        listbox_frame.columnconfigure(0, weight=1)
        listbox_frame.rowconfigure(0, weight=1)

        self.session_listbox = tk.Listbox(
            listbox_frame,
            selectmode=tk.SINGLE,
            font=("TkDefaultFont", 10),
            activestyle="none",
        )
        self.session_listbox.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # Add scrollbar
        scrollbar = ttk.Scrollbar(
            listbox_frame, orient=tk.VERTICAL, command=self.session_listbox.yview
        )
        scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
        self.session_listbox.configure(yscrollcommand=scrollbar.set)

        # Bind selection event
        self.session_listbox.bind("<<ListboxSelect>>", self._on_select)

    def update_sessions(self, sessions: list[tuple[str, str, str]]) -> None:
        """
        Update session list with new data.

        Args:
            sessions: List of (id, summary, status) tuples
        """
        try:
            # Store full session list
            self.sessions = sessions

            # Get current selection
            selected_idx = self.session_listbox.curselection()
            selected_id = None
            if selected_idx:
                selected_id = self.sessions[selected_idx[0]][0]

            # Clear listbox
            self.session_listbox.delete(0, tk.END)

            # Apply filter
            filtered_sessions = self._filter_sessions()

            # Add filtered sessions
            for _, summary, _ in filtered_sessions:
                self.session_listbox.insert(tk.END, summary)

            # Restore selection if possible
            if selected_id:
                for i, (session_id, _, _) in enumerate(filtered_sessions):
                    if session_id == selected_id:
                        self.session_listbox.selection_set(i)
                        self.session_listbox.see(i)
                        break

            # Auto-scroll to bottom for new sessions
            if not selected_id and filtered_sessions:
                self.session_listbox.see(tk.END)

        except Exception as e:
            logger.error(f"Error updating session list: {e}")

    def select_session(self, session_id: str) -> None:
        """
        Select session in list.

        Args:
            session_id: ID of session to select
        """
        try:
            filtered_sessions = self._filter_sessions()
            for i, (sid, _, _) in enumerate(filtered_sessions):
                if sid == session_id:
                    self.session_listbox.selection_clear(0, tk.END)
                    self.session_listbox.selection_set(i)
                    self.session_listbox.see(i)
                    break
        except Exception as e:
            logger.error(f"Error selecting session: {e}")

    def get_selected_session_id(self) -> str | None:
        """
        Get currently selected session ID.

        Returns:
            Session ID if selected, None otherwise
        """
        try:
            selection = self.session_listbox.curselection()
            if selection:
                filtered_sessions = self._filter_sessions()
                return filtered_sessions[selection[0]][0]
        except Exception as e:
            logger.error(f"Error getting selected session: {e}")
        return None

    def set_selection_callback(self, callback: Callable[[str], None]) -> None:
        """
        Set callback for session selection.

        Args:
            callback: Function taking session ID parameter
        """
        self.selection_callback = callback

    def _on_select(self, event) -> None:
        """
        Handle session selection event.

        Args:
            event: Selection event
        """
        if self.selection_callback:
            session_id = self.get_selected_session_id()
            if session_id:
                self.selection_callback(session_id)

    def _filter_sessions(self) -> list[tuple[str, str, str]]:
        """
        Apply current filter to sessions.

        Returns:
            Filtered list of sessions
        """
        filter_value = self.filter_var.get()
        if filter_value == "all":
            return self.sessions

        status_map = {
            "active": {"pending", "processing", "streaming"},
            "complete": {"complete"},
            "error": {"error"},
        }

        return [s for s in self.sessions if s[2] in status_map.get(filter_value, set())]

    def _apply_filter(self) -> None:
        """Apply current filter and update display."""
        self.update_sessions(self.sessions)

    def grid(self, **kwargs) -> None:
        """
        Grid the frame using provided arguments.

        Args:
            **kwargs: Grid configuration options
        """
        self.frame.grid(**kwargs)
