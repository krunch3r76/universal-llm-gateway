"""
Real-time streaming response display component.
"""

import tkinter as tk
from tkinter import scrolledtext, ttk

from universal_logging import get_logger

logger = get_logger(__name__)


class StreamDisplay:
    """
    Real-time streaming response display component.

    Features:
    - Auto-scrolling text display
    - Configurable styling
    - Copy support
    - Clear functionality
    """

    def __init__(self, parent, bg_color="#d4edda"):
        """
        Initialize stream display.

        Args:
            parent: Parent tkinter widget
            bg_color: Background color for text area
        """
        self.parent = parent
        self.frame = ttk.Frame(parent)
        self.text_widget = None
        self.setup_ui(bg_color)

    def setup_ui(self, bg_color: str) -> None:
        """
        Create text widget with styling.

        Args:
            bg_color: Background color for text area
        """
        # Create main container
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(0, weight=1)

        # Create scrolled text widget
        self.text_widget = scrolledtext.ScrolledText(
            self.frame,
            wrap=tk.WORD,
            background=bg_color,
            font=("TkDefaultFont", 10),
            padx=5,
            pady=5,
        )
        self.text_widget.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # Configure text widget
        self.text_widget.configure(state="disabled")  # Start in disabled state

        # Create right-click menu
        self.context_menu = tk.Menu(self.frame, tearoff=0)
        self.context_menu.add_command(label="Copy", command=self.copy_text)
        self.context_menu.add_command(label="Clear", command=self.clear)

        # Bind right-click
        self.text_widget.bind("<Button-3>", self.show_context_menu)

        # Bind Ctrl+C
        self.text_widget.bind("<Control-c>", lambda e: self.copy_text())

    def append_chunk(self, chunk: str) -> None:
        """
        Append new chunk to display.

        Args:
            chunk: Text chunk to append
        """
        try:
            self.text_widget.configure(state="normal")
            self.text_widget.insert(tk.END, chunk)
            self.text_widget.configure(state="disabled")
            self.text_widget.see(tk.END)  # Auto-scroll to bottom
        except Exception as e:
            logger.error(f"Error appending chunk: {e}")

    def set_content(self, content: str) -> None:
        """
        Set complete content.

        Args:
            content: Complete text content
        """
        try:
            self.text_widget.configure(state="normal")
            self.text_widget.delete("1.0", tk.END)
            self.text_widget.insert("1.0", content)
            self.text_widget.configure(state="disabled")
            self.text_widget.see(tk.END)
        except Exception as e:
            logger.error(f"Error setting content: {e}")

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
