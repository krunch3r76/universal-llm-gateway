"""
Session metadata and status display component.
"""

import tkinter as tk
from datetime import datetime
from tkinter import ttk

from universal_logging import get_logger

from ...model.data_structures import RequestSession

logger = get_logger(__name__)


class InfoPanel:
    """
    Session metadata and status display component.

    Features:
    - Status display with icons
    - Timing information
    - Request metadata
    - Error details when applicable
    """

    def __init__(self, parent):
        """
        Initialize info panel.

        Args:
            parent: Parent tkinter widget
        """
        self.parent = parent
        self.frame = ttk.Frame(parent)
        self.labels: dict[str, ttk.Label] = {}
        self.setup_ui()

    def setup_ui(self) -> None:
        """Create info panel with labels."""
        # Configure frame
        self.frame.columnconfigure(1, weight=1)  # Value column stretches

        # Status row
        self._add_label_pair("Status", "status", row=0)

        # Timing row
        self._add_label_pair("Duration", "duration", row=1)

        # Model row
        self._add_label_pair("Model", "model", row=2)

        # Token metrics row
        self._add_label_pair("Tokens", "tokens", row=3)

        # Error row (hidden by default)
        self._add_label_pair("Error", "error", row=4)
        self.labels["error_row"].grid_remove()
        self.labels["error"].grid_remove()

        # Style status label
        self.labels["status"].configure(font=("TkDefaultFont", 10, "bold"))

    def _add_label_pair(self, title: str, key: str, row: int) -> None:
        """
        Add a label pair (title: value) to the panel.

        Args:
            title: Label title
            key: Dictionary key for the value label
            row: Grid row number
        """
        # Title label
        title_label = ttk.Label(
            self.frame, text=f"{title}:", font=("TkDefaultFont", 10)
        )
        title_label.grid(row=row, column=0, sticky=tk.W, padx=(5, 2), pady=2)
        self.labels[f"{key}_row"] = title_label

        # Value label
        value_label = ttk.Label(self.frame, text="", font=("TkDefaultFont", 10))
        value_label.grid(row=row, column=1, sticky=tk.W, padx=(2, 5), pady=2)
        self.labels[key] = value_label

    def update_info(self, session: RequestSession) -> None:
        """
        Update displayed metadata.

        Args:
            session: RequestSession to display info for
        """
        try:
            # Update status with icon
            status_icons = {
                "pending": "⏳",
                "processing": "🔄",
                "streaming": "📝",
                "complete": "✅",
                "error": "❌",
            }
            status_text = (
                f"{status_icons.get(session.status, '?')} {session.status.title()}"
            )
            self.labels["status"].configure(text=status_text)

            # Update duration
            if session.processing_time_ms is not None:
                duration = f"{session.processing_time_ms / 1000:.2f}s"
            elif "start_time" in session.metadata:
                elapsed = (
                    datetime.now() - session.metadata["start_time"]
                ).total_seconds()
                duration = f"{elapsed:.2f}s"
            else:
                duration = "N/A"
            self.labels["duration"].configure(text=duration)

            # Update model info
            model = session.original_request.get("model", "N/A")
            self.labels["model"].configure(text=model)

            # Update token metrics
            if "token_metrics" in session.metadata:
                metrics = session.metadata["token_metrics"]
                inp = metrics.get("input", 0)
                out = metrics.get("output", 0)
                tokens = f"Input: {inp} | Output: {out}"
            else:
                tokens = "N/A"
            self.labels["tokens"].configure(text=tokens)

            # Update error info if present
            if session.status == "error" and "error" in session.metadata:
                self.labels["error_row"].grid()
                self.labels["error"].grid()
                self.labels["error"].configure(
                    text=str(session.metadata["error"]), foreground="red"
                )
            else:
                self.labels["error_row"].grid_remove()
                self.labels["error"].grid_remove()

        except Exception as e:
            logger.error(f"Error updating info panel: {e}")

    def clear(self) -> None:
        """Clear all displayed data."""
        try:
            for key in ["status", "duration", "model", "tokens"]:
                self.labels[key].configure(text="")
            self.labels["error_row"].grid_remove()
            self.labels["error"].grid_remove()
        except Exception as e:
            logger.error(f"Error clearing info panel: {e}")

    def grid(self, **kwargs) -> None:
        """
        Grid the frame using provided arguments.

        Args:
            **kwargs: Grid configuration options
        """
        self.frame.grid(**kwargs)
