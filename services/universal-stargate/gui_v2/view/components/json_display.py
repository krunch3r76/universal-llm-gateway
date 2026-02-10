"""
JSON data display component with formatting and syntax highlighting.
"""

import json
import tkinter as tk
from tkinter import scrolledtext, ttk
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


class JsonDisplay:
    """
    JSON data display component.

    Features:
    - Pretty printing
    - Syntax highlighting
    - Copy support
    - Clear functionality
    """

    def __init__(self, parent, title: str = "", bg_color: str = "#ffffff"):
        """
        Initialize JSON display.

        Args:
            parent: Parent tkinter widget
            title: Optional title for the display
            bg_color: Background color for text area
        """
        self.parent = parent
        self.title = title
        self.frame = ttk.Frame(parent)
        self.text_widget = None
        self.setup_ui(bg_color)

    def setup_ui(self, bg_color: str) -> None:
        """
        Create text widget with styling.

        Args:
            bg_color: Background color for text area
        """
        # Configure frame
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)

        # Add title if provided
        if self.title:
            title_label = ttk.Label(
                self.frame, text=self.title, font=("TkDefaultFont", 10, "bold")
            )
            title_label.grid(row=0, column=0, sticky=tk.W, padx=5, pady=(5, 0))

        # Create scrolled text widget
        self.text_widget = scrolledtext.ScrolledText(
            self.frame,
            wrap=tk.NONE,  # No word wrap for JSON
            background=bg_color,
            font=("Courier", 10),  # Monospace font for JSON
            padx=5,
            pady=5,
        )
        self.text_widget.grid(
            row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=5, pady=5
        )

        # Configure text widget
        self.text_widget.configure(state="disabled")

        # Create horizontal scrollbar
        h_scrollbar = ttk.Scrollbar(
            self.frame, orient=tk.HORIZONTAL, command=self.text_widget.xview
        )
        h_scrollbar.grid(row=2, column=0, sticky=(tk.W, tk.E))
        self.text_widget.configure(xscrollcommand=h_scrollbar.set)

        # Create right-click menu
        self.context_menu = tk.Menu(self.frame, tearoff=0)
        self.context_menu.add_command(label="Copy", command=self.copy_text)
        self.context_menu.add_command(label="Clear", command=self.clear)

        # Bind right-click
        self.text_widget.bind("<Button-3>", self.show_context_menu)

        # Bind Ctrl+C
        self.text_widget.bind("<Control-c>", lambda e: self.copy_text())

    def set_json(self, data: Any) -> None:
        """
        Set JSON data with pretty printing.

        Args:
            data: Data to display as JSON
        """
        try:
            # Convert to JSON with pretty printing
            if isinstance(data, (dict, list)):
                json_str = json.dumps(data, indent=2, ensure_ascii=False)
            else:
                json_str = str(data)

            # Update display
            self.text_widget.configure(state="normal")
            self.text_widget.delete("1.0", tk.END)
            self.text_widget.insert("1.0", json_str)
            self.text_widget.configure(state="disabled")

        except Exception as e:
            logger.error(f"Error setting JSON data: {e}")
            self.text_widget.configure(state="normal")
            self.text_widget.delete("1.0", tk.END)
            self.text_widget.insert("1.0", f"Error displaying data: {e}")
            self.text_widget.configure(state="disabled")

    def clear(self) -> None:
        """Clear display."""
        try:
            self.text_widget.configure(state="normal")
            self.text_widget.delete("1.0", tk.END)
            self.text_widget.configure(state="disabled")
        except Exception as e:
            logger.error(f"Error clearing display: {e}")

    def copy_text(self) -> None:
        """Copy selected text or all text to clipboard."""
        try:
            # Get selected text or all text
            if self.text_widget.tag_ranges(tk.SEL):
                text = self.text_widget.get(tk.SEL_FIRST, tk.SEL_LAST)
            else:
                text = self.text_widget.get("1.0", tk.END)

            # Copy to clipboard
            self.frame.clipboard_clear()
            self.frame.clipboard_append(text)
        except Exception as e:
            logger.error(f"Error copying text: {e}")

    def show_context_menu(self, event) -> None:
        """
        Show context menu on right-click.

        Args:
            event: Mouse event
        """
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.context_menu.grab_release()

    def grid(self, **kwargs) -> None:
        """
        Grid the frame using provided arguments.

        Args:
            **kwargs: Grid configuration options
        """
        self.frame.grid(**kwargs)
