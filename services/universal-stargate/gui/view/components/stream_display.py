"""
Stream display widget for showing streaming response data.

Specialized widget for displaying streaming responses with chunk information
and content reconstruction.
"""

import tkinter as tk
from tkinter import scrolledtext, ttk

from universal_logging import get_logger

from ...model.data_structures import ParsedResponse

# Get dedicated GUI chunk debug logger (auto-initialized)
gui_chunk_logger = get_logger("gui_chunk_debug")

logger = get_logger(__name__)


class StreamDisplay:
    """Widget for displaying streaming response data"""

    def __init__(self, parent, title: str, background_color: str = "#ffffff"):
        """
        Initialize stream display widget.

        Args:
            parent: Parent tkinter widget
            title: Title for the display panel
            background_color: Background color for the text area
        """
        self.parent = parent
        self.title = title
        self.background_color = background_color

        # Track accumulated chunks for real-time updates
        self.accumulated_chunks = []
        self.current_event_id = None
        self.current_original_request = None
        self.current_request_id = None

        # Create the frame
        self.frame = ttk.LabelFrame(parent, text=title, padding="5")

        # Configure grid
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=0)  # Button row

        # Create notebook for tabs
        self.notebook = ttk.Notebook(self.frame)
        self.notebook.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # Create content tab
        self.content_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.content_frame, text="Content")

        # Create content text area
        self.content_frame.columnconfigure(0, weight=1)
        self.content_frame.rowconfigure(0, weight=1)

        self.content_text = scrolledtext.ScrolledText(
            self.content_frame,
            wrap=tk.WORD,
            font=("Consolas", 10),
            state=tk.DISABLED,
            bg=background_color,
            fg="#212529",
            insertbackground="#212529",
            selectbackground="#007acc",
        )
        self.content_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # Create chunks tab
        self.chunks_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.chunks_frame, text="Chunks")

        # Create chunks text area
        self.chunks_frame.columnconfigure(0, weight=1)
        self.chunks_frame.rowconfigure(0, weight=1)

        self.chunks_text = scrolledtext.ScrolledText(
            self.chunks_frame,
            wrap=tk.WORD,
            font=("Consolas", 9),
            state=tk.DISABLED,
            bg="#f8f9fa",
            fg="#495057",
        )
        self.chunks_text.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # Create button frame
        button_frame = ttk.Frame(self.frame)
        button_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(5, 0))

        # Create copy button
        self.copy_button = ttk.Button(
            button_frame, text="Copy Content", command=self.copy_content
        )
        self.copy_button.pack(side=tk.RIGHT, padx=(5, 0))

        # Create copy chunks button
        self.copy_chunks_button = ttk.Button(
            button_frame, text="Copy Chunks", command=self.copy_chunks
        )
        self.copy_chunks_button.pack(side=tk.RIGHT)

        # Create status label
        self.status_label = ttk.Label(button_frame, text="")
        self.status_label.pack(side=tk.LEFT)

    def update(
        self,
        parsed_response: ParsedResponse,
        event_id: str = None,
        original_request: str = None,
        request_id: str = None,
    ):
        """
        Update the display with new streaming response.

        Args:
            parsed_response: ParsedResponse object for streaming data
            event_id: Event ID for detecting new requests
            original_request: Original request content for detecting new requests
            request_id: Request ID for detecting new requests (1-to-many with event_id)
        """
        try:
            # Handle real-time chunk updates
            if parsed_response.response_type == "streaming_chunk":
                # This is a real-time chunk update
                self._handle_realtime_chunk(
                    parsed_response, event_id, original_request, request_id
                )
                return

            # Handle regular streaming response updates
            # Check if this is a new event (different event ID)
            if event_id and event_id != self.current_event_id:
                # New event, reset accumulated chunks
                self.accumulated_chunks = []
                self.current_event_id = event_id

            # Update content tab
            self._update_content(parsed_response.formatted_text)

            # Update chunks tab
            if parsed_response.chunks:
                self._update_chunks(parsed_response.chunks)
                self.status_label.config(text=f"{len(parsed_response.chunks)} chunks")
            else:
                self._update_chunks([])
                self.status_label.config(text="Streaming - chunks not captured")

            # Update tab labels with counts
            if parsed_response.chunks:
                self.notebook.tab(
                    0, text=f"Content ({len(parsed_response.chunks)} chunks)"
                )
                self.notebook.tab(1, text=f"Chunks ({len(parsed_response.chunks)})")
            else:
                self.notebook.tab(0, text="Content")
                self.notebook.tab(1, text="Chunks (not captured)")

        except Exception as e:
            logger.error(f"Error updating stream display: {e}")
            self._show_error(f"Error displaying streaming content: {e}")

    def _update_content(self, content: str):
        """Update the content tab efficiently with scroll position preservation"""
        self.content_text.config(state=tk.NORMAL)

        # Get current content and scroll position
        current_text = self.content_text.get(1.0, tk.END).rstrip()
        current_length = len(current_text)

        # Check if user is scrolled to bottom (within 2 lines)
        # yview returns (top_fraction, bottom_fraction) of visible area
        yview = self.content_text.yview()
        at_bottom = yview[1] >= 0.98  # Consider "at bottom" if within 2% of end

        # Only update if content changed
        if content and content != current_text:
            # For streaming: append only new content if it's an extension
            if content.startswith(current_text) and len(content) > current_length:
                # Append only the new part
                new_content = content[current_length:]
                self.content_text.insert(tk.END, new_content)
                # Auto-scroll to bottom only if already at bottom
                if at_bottom:
                    self.content_text.see(tk.END)
            else:
                # Full replacement (for navigation or completed responses)
                # Save scroll position before replacement
                scroll_pos = self.content_text.yview()[0]

                self.content_text.delete(1.0, tk.END)
                self.content_text.insert(1.0, content or "No content")

                # Restore scroll position (scroll to same relative position)
                self.content_text.yview_moveto(scroll_pos)

        self.content_text.config(state=tk.DISABLED)

    def _update_chunks(self, chunks: list):
        """Update the chunks tab efficiently"""
        self.chunks_text.config(state=tk.NORMAL)

        # Get current chunk count
        current_text = self.chunks_text.get(1.0, tk.END)
        current_chunk_count = current_text.count("Chunk ")

        # Only update if number of chunks changed
        if len(chunks) != current_chunk_count:
            self.chunks_text.delete(1.0, tk.END)

            if chunks:
                for i, chunk in enumerate(chunks, 1):
                    self.chunks_text.insert(tk.END, f"Chunk {i}:\n{chunk}\n\n")
            else:
                # Show a more informative message for streaming responses
                self.chunks_text.insert(1.0, "Streaming Response - Chunks Tab\n\n")
                self.chunks_text.insert(
                    tk.END,
                    "Chunks are captured during streaming and displayed here.\n",
                )
                self.chunks_text.insert(tk.END, "If you see this message, it means:\n")
                self.chunks_text.insert(
                    tk.END, "• The stream is still in progress, or\n"
                )
                self.chunks_text.insert(
                    tk.END, "• Chunks were not captured during streaming\n\n"
                )
                self.chunks_text.insert(
                    tk.END, "For real-time chunk monitoring, the chunks are captured\n"
                )
                self.chunks_text.insert(
                    tk.END, "as the stream is consumed by the client.\n"
                )
                self.chunks_text.insert(
                    tk.END, "This is a limitation of the current monitoring system.\n\n"
                )
                self.chunks_text.insert(
                    tk.END, "To see the actual chunks, you would need to:\n"
                )
                self.chunks_text.insert(
                    tk.END, "1. Monitor the network traffic directly, or\n"
                )
                self.chunks_text.insert(
                    tk.END, "2. Use a different monitoring approach"
                )

        self.chunks_text.config(state=tk.DISABLED)

    def _handle_realtime_chunk(
        self,
        parsed_response: ParsedResponse,
        event_id: str = None,
        original_request: str = None,
        request_id: str = None,
    ):
        """Handle real-time chunk updates"""
        try:
            # Check if this is a new request by comparing the request ID
            if not self.current_request_id:
                # First request
                self.current_request_id = request_id
                self.current_event_id = event_id
                gui_chunk_logger.info(f"First request detected: {request_id}")
            elif request_id and request_id != self.current_request_id:
                # New request detected, reset accumulated chunks
                gui_chunk_logger.info(
                    f"New request detected: {request_id}, resetting chunks"
                )
                self.accumulated_chunks = []
                self.current_request_id = request_id
                self.current_event_id = event_id

            # Process new chunks
            if parsed_response.chunks:
                # Add to accumulated chunks list (for chunks tab only)
                self.accumulated_chunks.extend(parsed_response.chunks)

                # Only log the last chunk streamed, not the whole list
                if parsed_response.chunks:
                    last_chunk_preview = (
                        parsed_response.chunks[-1][:50]
                        if len(parsed_response.chunks[-1]) > 50
                        else parsed_response.chunks[-1]
                    )
                    count = len(self.accumulated_chunks)
                    gui_chunk_logger.debug(
                        f"Last chunk: '{last_chunk_preview}...', total: {count}"
                    )

                # Update content tab with accumulated content
                accumulated_content = "".join(self.accumulated_chunks)
                self._update_content(accumulated_content)

            # Update chunks tab with all accumulated chunks
            self._update_chunks(self.accumulated_chunks)

            # Update status and tab labels
            chunk_count = len(self.accumulated_chunks)
            self.status_label.config(text=f"{chunk_count} chunks (live)")
            self.notebook.tab(0, text=f"Content ({chunk_count} chunks)")
            self.notebook.tab(1, text=f"Chunks ({chunk_count})")

        except Exception as e:
            gui_chunk_logger.error(f"Error handling real-time chunk: {e}")
            self._show_error(f"Error handling real-time chunk: {e}")

    def _show_error(self, error_msg: str):
        """Show error in both tabs"""
        # Content tab
        self.content_text.config(state=tk.NORMAL)
        self.content_text.delete(1.0, tk.END)
        self.content_text.insert(1.0, error_msg)
        self.content_text.config(state=tk.DISABLED)

        # Chunks tab
        self.chunks_text.config(state=tk.NORMAL)
        self.chunks_text.delete(1.0, tk.END)
        self.chunks_text.insert(1.0, error_msg)
        self.chunks_text.config(state=tk.DISABLED)

        self.status_label.config(text="Error")

    def copy_content(self):
        """Copy current content to clipboard"""
        try:
            content = self.content_text.get(1.0, tk.END)
            self.parent.clipboard_clear()
            self.parent.clipboard_append(content)

            # Show confirmation
            original_text = self.copy_button.config("text")[-1]
            self.copy_button.config(text="Copied!")
            self.parent.after(1000, lambda: self.copy_button.config(text=original_text))

        except Exception as e:
            logger.error(f"Error copying content: {e}")

    def copy_chunks(self):
        """Copy chunks to clipboard"""
        try:
            chunks = self.chunks_text.get(1.0, tk.END)
            self.parent.clipboard_clear()
            self.parent.clipboard_append(chunks)

            # Show confirmation
            original_text = self.copy_chunks_button.config("text")[-1]
            self.copy_chunks_button.config(text="Copied!")
            self.parent.after(
                1000, lambda: self.copy_chunks_button.config(text=original_text)
            )

        except Exception as e:
            logger.error(f"Error copying chunks: {e}")

    def grid(self, **kwargs):
        """Grid the widget frame"""
        self.frame.grid(**kwargs)

    def grid_remove(self):
        """Remove the widget from grid"""
        self.frame.grid_remove()
