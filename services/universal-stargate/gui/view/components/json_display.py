"""
JSON display widget for showing formatted JSON data.

Provides a reusable widget for displaying JSON data with syntax highlighting
and copy functionality. Auto-truncates large JSON field values for performance
while preserving JSON structure.
"""

import json
import tkinter as tk
from tkinter import scrolledtext, ttk

from universal_logging import get_logger, truncate_json_fields

logger = get_logger(__name__)

# Truncation thresholds for JSON field values (for GUI display)
FIELD_TRUNCATE_THRESHOLD = (
    2000  # Truncate individual JSON string fields larger than this
)
FIELD_PREVIEW_HEAD = 1000  # Show first N chars of truncated field
FIELD_PREVIEW_TAIL = 200  # Show last N chars of truncated field


class JsonDisplay:
    """Widget for displaying formatted JSON data with smart truncation"""

    def __init__(self, parent, title: str, background_color: str = "#ffffff"):
        """
        Initialize JSON display widget.

        Args:
            parent: Parent tkinter widget
            title: Title for the display panel
            background_color: Background color for the text area
        """
        self.parent = parent
        self.title = title
        self.background_color = background_color

        # Store original full content for "View Full" functionality
        self.full_content = None
        self.is_truncated = False

        # Create the frame
        self.frame = ttk.LabelFrame(parent, text=title, padding="5")

        # Configure grid
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=0)  # Button row

        # Create text area
        self.text_area = scrolledtext.ScrolledText(
            self.frame,
            wrap=tk.WORD,
            font=("Consolas", 10),
            state=tk.DISABLED,
            bg=background_color,
            fg="#212529",
            insertbackground="#212529",
            selectbackground="#007acc",
        )
        self.text_area.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # Create button frame
        button_frame = ttk.Frame(self.frame)
        button_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(5, 0))

        # Create view full button (initially hidden)
        self.view_full_button = ttk.Button(
            button_frame, text="View Full", command=self.view_full_content
        )

        # Create copy button
        self.copy_button = ttk.Button(
            button_frame, text="Copy", command=self.copy_content
        )
        self.copy_button.pack(side=tk.RIGHT)

        # Create status label
        self.status_label = ttk.Label(button_frame, text="")
        self.status_label.pack(side=tk.LEFT)

    def update(self, content: str):
        """
        Update the display with new content, preserving scroll position.
        Automatically truncates large JSON field values for performance.

        Args:
            content: New content to display
        """
        try:
            # Store original full content
            self.full_content = content
            self.is_truncated = False

            # Try to parse as JSON and truncate fields
            display_content = content
            if content and content.strip():
                try:
                    # Parse JSON
                    json_obj = json.loads(content)

                    # Truncate large fields
                    truncated_obj = truncate_json_fields(
                        json_obj,
                        max_field_size=FIELD_TRUNCATE_THRESHOLD,
                        head_chars=FIELD_PREVIEW_HEAD,
                        tail_chars=FIELD_PREVIEW_TAIL,
                    )

                    # Re-serialize with pretty formatting
                    display_content = json.dumps(
                        truncated_obj, indent=2, ensure_ascii=False
                    )

                    # Check if truncation occurred
                    if display_content != content:
                        self.is_truncated = True

                except (json.JSONDecodeError, TypeError):
                    # Not valid JSON or can't serialize, use as-is
                    display_content = content

            # Enable editing temporarily
            self.text_area.config(state=tk.NORMAL)

            # Get current content and scroll position before update
            current_content = self.text_area.get(1.0, tk.END).rstrip()
            scroll_pos = self.text_area.yview()[0]

            # Only update if content has changed
            new_content = display_content if display_content else "No data"
            if new_content != current_content:
                # Clear and insert new content
                self.text_area.delete(1.0, tk.END)
                self.text_area.insert(1.0, new_content)

                # Restore scroll position
                self.text_area.yview_moveto(scroll_pos)

            # Update status label and view full button
            if content:
                size_kb = len(content) / 1024
                lines = len(display_content.splitlines())
                status = f"{lines} lines, {size_kb:.1f} KB"
                if self.is_truncated:
                    status += " (truncated)"
                self.status_label.config(text=status)

                # Show/hide "View Full" button
                if self.is_truncated:
                    self.view_full_button.pack(side=tk.RIGHT, padx=(0, 5))
                else:
                    self.view_full_button.pack_forget()
            else:
                self.status_label.config(text="Empty")
                self.view_full_button.pack_forget()

            # Disable editing
            self.text_area.config(state=tk.DISABLED)

        except Exception as e:
            logger.error(f"Error updating JSON display: {e}", exc_info=True)
            self.text_area.config(state=tk.NORMAL)
            self.text_area.delete(1.0, tk.END)
            self.text_area.insert(1.0, f"Error displaying content: {e}")
            self.text_area.config(state=tk.DISABLED)
            self.status_label.config(text="Error")
            self.view_full_button.pack_forget()

    def view_full_content(self):
        """Open full content in separate optimized viewer window"""
        if not self.full_content:
            return

        try:
            # Create new top-level window
            viewer = tk.Toplevel(self.parent)
            viewer.title(f"{self.title} - Full Content")
            viewer.geometry("1000x700")

            # Configure grid
            viewer.columnconfigure(0, weight=1)
            viewer.rowconfigure(0, weight=1)
            viewer.rowconfigure(1, weight=0)

            # Create text widget with no word wrap for better performance
            text_widget = scrolledtext.ScrolledText(
                viewer,
                wrap=tk.NONE,  # No word wrap for performance
                font=("Consolas", 10),
                bg=self.background_color,
                fg="#212529",
            )
            text_widget.grid(
                row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=10, pady=10
            )
            text_widget.insert(1.0, self.full_content)
            text_widget.config(state=tk.DISABLED)

            # Create button frame
            button_frame = ttk.Frame(viewer)
            button_frame.grid(
                row=1, column=0, sticky=(tk.W, tk.E), padx=10, pady=(0, 10)
            )

            # Add copy button
            def copy_full():
                viewer.clipboard_clear()
                viewer.clipboard_append(self.full_content)
                copy_btn.config(text="Copied!")
                viewer.after(1000, lambda: copy_btn.config(text="Copy Full Content"))

            copy_btn = ttk.Button(
                button_frame, text="Copy Full Content", command=copy_full
            )
            copy_btn.pack(side=tk.RIGHT, padx=5)

            # Add info label
            size_kb = len(self.full_content) / 1024
            lines = len(self.full_content.splitlines())
            info_label = ttk.Label(
                button_frame, text=f"{lines:,} lines, {size_kb:.1f} KB"
            )
            info_label.pack(side=tk.LEFT)

            # Add close button
            close_btn = ttk.Button(button_frame, text="Close", command=viewer.destroy)
            close_btn.pack(side=tk.RIGHT)

        except Exception as e:
            logger.error(f"Error opening full content viewer: {e}", exc_info=True)

    def copy_content(self):
        """Copy current displayed content to clipboard"""
        try:
            content = self.text_area.get(1.0, tk.END)
            self.parent.clipboard_clear()
            self.parent.clipboard_append(content)

            # Show confirmation
            original_text = self.copy_button.config("text")[-1]
            self.copy_button.config(text="Copied!")
            self.parent.after(1000, lambda: self.copy_button.config(text=original_text))

        except Exception as e:
            logger.error(f"Error copying content: {e}")

    def grid(self, **kwargs):
        """Grid the widget frame"""
        self.frame.grid(**kwargs)

    def grid_remove(self):
        """Remove the widget from grid"""
        self.frame.grid_remove()

    def get_content(self) -> str:
        """Get current content"""
        return self.text_area.get(1.0, tk.END)
