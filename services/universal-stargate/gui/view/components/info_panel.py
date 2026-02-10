"""
Info panel widget for displaying event metadata.

Shows key information about universal_stargate events including timestamps,
processing times, and stargate actions.
"""

import tkinter as tk
from tkinter import ttk

from universal_logging import get_logger

logger = get_logger(__name__)


class InfoPanel:
    """Widget for displaying event metadata and information"""

    def __init__(self, parent):
        """
        Initialize info panel widget.

        Args:
            parent: Parent tkinter widget
        """
        self.parent = parent

        # Create main frame
        self.frame = ttk.Frame(parent)

        # Configure grid weights
        self.frame.columnconfigure(0, weight=1)
        self.frame.columnconfigure(1, weight=1)

        # Create info variables
        self.info_vars = {
            "id": tk.StringVar(value="Request ID: -"),
            "timestamp": tk.StringVar(value="Timestamp: -"),
            "type": tk.StringVar(value="Type: -"),
            "processing_time": tk.StringVar(value="Processing Time: -"),
            "gateway": tk.StringVar(value="Gateway: -"),
            "actions": tk.StringVar(value="Actions: -"),
        }

        self._create_info_labels()

    def _create_info_labels(self):
        """Create the information labels"""
        # Row 0: Request ID and Timestamp
        id_label = ttk.Label(
            self.frame, textvariable=self.info_vars["id"], font=("Segoe UI", 10, "bold")
        )
        id_label.grid(row=0, column=0, sticky=tk.W, padx=(0, 10))

        timestamp_label = ttk.Label(
            self.frame, textvariable=self.info_vars["timestamp"], font=("Segoe UI", 10)
        )
        timestamp_label.grid(row=0, column=1, sticky=tk.W)

        # Row 1: Type and Processing Time
        type_label = ttk.Label(
            self.frame, textvariable=self.info_vars["type"], font=("Segoe UI", 10)
        )
        type_label.grid(row=1, column=0, sticky=tk.W, padx=(0, 10))

        processing_label = ttk.Label(
            self.frame,
            textvariable=self.info_vars["processing_time"],
            font=("Segoe UI", 10),
        )
        processing_label.grid(row=1, column=1, sticky=tk.W)

        # Row 2: Gateway
        gateway_label = ttk.Label(
            self.frame, textvariable=self.info_vars["gateway"], font=("Segoe UI", 10)
        )
        gateway_label.grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=(5, 0))

        # Row 3: Stargate Actions
        actions_label = ttk.Label(
            self.frame,
            textvariable=self.info_vars["actions"],
            font=("Segoe UI", 10, "bold"),
            foreground="blue",
        )
        actions_label.grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=(5, 0))

    def update(self, event_info: dict[str, str]):
        """
        Update the info panel with new event information.

        Args:
            event_info: Dictionary containing event information
        """
        try:
            # Update all info variables
            for key, var in self.info_vars.items():
                if key in event_info:
                    value = event_info[key]

                    # Format the display value
                    if key == "id":
                        var.set(f"Request ID: {value}")
                    elif key == "timestamp":
                        var.set(f"Timestamp: {value}")
                    elif key == "type":
                        # Special handling for pre-processing events
                        if value == "pre_processing":
                            var.set("Type: ⏳ Pre-processing")
                        else:
                            var.set(f"Type: {value}")
                    elif key == "processing_time":
                        var.set(f"Processing Time: {value}")
                    elif key == "gateway":
                        var.set(f"Gateway: {value}")
                    elif key == "actions":
                        var.set(f"Actions: {value}")
                else:
                    # Set default value if key not found
                    if key == "id":
                        var.set("Request ID: -")
                    elif key == "timestamp":
                        var.set("Timestamp: -")
                    elif key == "type":
                        var.set("Type: -")
                    elif key == "processing_time":
                        var.set("Processing Time: -")
                    elif key == "gateway":
                        var.set("Gateway: -")
                    elif key == "actions":
                        var.set("Actions: -")

        except Exception as e:
            logger.error(f"Error updating info panel: {e}")
            self._show_error()

    def _show_error(self):
        """Show error state in all labels"""
        for key, var in self.info_vars.items():
            var.set(f"{key.title()}: Error")

    def clear(self):
        """Clear all information"""
        for key, var in self.info_vars.items():
            if key == "id":
                var.set("Request ID: -")
            elif key == "timestamp":
                var.set("Timestamp: -")
            elif key == "type":
                var.set("Type: -")
            elif key == "processing_time":
                var.set("Processing Time: -")
            elif key == "gateway":
                var.set("Gateway: -")
            elif key == "actions":
                var.set("Actions: -")

    def grid(self, **kwargs):
        """Grid the widget frame"""
        self.frame.grid(**kwargs)

    def grid_remove(self):
        """Remove the widget from grid"""
        self.frame.grid_remove()

    def get_info(self) -> dict[str, str]:
        """
        Get current info panel content.

        Returns:
            Dictionary with current info values
        """
        return {key: var.get() for key, var in self.info_vars.items()}
