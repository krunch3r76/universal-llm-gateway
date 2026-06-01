"""
Detailed session view with request, response, and metadata.
"""

import tkinter as tk
from tkinter import ttk

from universal_logging import get_logger

from ..model.data_structures import RequestSession
from .components import InfoPanel, JsonDisplay, StreamDisplay

logger = get_logger(__name__)


class SessionDetailView:
    """
    Detailed view of selected session.

    Features:
    - Tabbed interface
    - Request/response display
    - Metadata panel
    - Real-time updates
    """

    def __init__(self, parent):
        """
        Initialize session detail view.

        Args:
            parent: Parent tkinter widget
        """
        self.parent = parent
        self.frame = ttk.Frame(parent)
        self.notebook = None
        self.request_frame = None
        self.response_frame = None
        self.metadata_frame = None
        self.stream_display = None
        self.json_display = None
        self.info_panel = None
        self.setup_ui()

    def setup_ui(self) -> None:
        """Create tabbed view with request, response, and metadata."""
        # Configure frame
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)

        # Create info panel
        self.info_panel = InfoPanel(self.frame)
        self.info_panel.grid(row=0, column=0, sticky=(tk.W, tk.E), padx=5, pady=5)

        # Create notebook for tabs
        self.notebook = ttk.Notebook(self.frame)
        self.notebook.grid(
            row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=5, pady=5
        )

        # Create request tab
        self.request_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.request_frame, text="Request")

        # Configure request frame
        self.request_frame.columnconfigure(0, weight=1)
        self.request_frame.rowconfigure(0, weight=1)
        self.request_frame.rowconfigure(1, weight=1)

        # Add original and modified request displays
        self.original_request = JsonDisplay(
            self.request_frame, title="Original Request", bg_color="#f8f9fa"
        )
        self.original_request.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        self.modified_request = JsonDisplay(
            self.request_frame, title="Modified Request", bg_color="#fff3cd"
        )
        self.modified_request.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # Create response tab
        self.response_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.response_frame, text="Response")

        # Configure response frame
        self.response_frame.columnconfigure(0, weight=1)
        self.response_frame.rowconfigure(0, weight=1)

        # Create response container that can switch between JSON and Stream
        self.response_container = ttk.Frame(self.response_frame)
        self.response_container.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.response_container.columnconfigure(0, weight=1)
        self.response_container.rowconfigure(0, weight=1)

        # Create both response displays
        self.json_display = JsonDisplay(
            self.response_container, title="Complete Response", bg_color="#d4edda"
        )
        self.stream_display = StreamDisplay(self.response_container, bg_color="#d4edda")

        # Initially show JSON display
        self.json_display.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.current_response_view = "json"

        # Create metadata tab
        self.metadata_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.metadata_frame, text="Metadata")

        # Configure metadata frame
        self.metadata_frame.columnconfigure(0, weight=1)
        self.metadata_frame.rowconfigure(0, weight=1)

        # Add metadata display
        self.metadata_display = JsonDisplay(
            self.metadata_frame, title="Session Metadata", bg_color="#fffff"
        )
        self.metadata_display.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

    def update_session(self, session: RequestSession) -> None:
        """
        Update view with session data.

        Args:
            session: RequestSession to display
        """
        try:
            # Update info panel
            self.info_panel.update_info(session)

            # Update request displays
            self.original_request.set_json(session.original_request)
            self.modified_request.set_json(session.modified_request)

            # Update response display based on status
            if session.status == "streaming":
                # Show stream display during streaming
                if self.current_response_view != "stream":
                    self.json_display.grid_remove()
                    self.stream_display.grid(
                        row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S)
                    )
                    self.current_response_view = "stream"
                # Append new chunk if available
                if session.response_chunks:
                    latest_chunk = session.response_chunks[-1]
                    chunk_content = latest_chunk.get("chunk", {}).get("content", "")
                    if chunk_content:
                        self.stream_display.append_chunk(chunk_content)
            else:
                # Show JSON display for complete/error state
                if self.current_response_view != "json":
                    self.stream_display.grid_remove()
                    self.json_display.grid(
                        row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S)
                    )
                    self.current_response_view = "json"
                # Update complete response
                if session.complete_response:
                    self.json_display.set_json(session.complete_response)

            # Update metadata display
            metadata = {
                "id": session.id,
                "timestamp": session.timestamp.isoformat(),
                "status": session.status,
                "processing_time_ms": session.processing_time_ms,
                **session.metadata,
            }
            self.metadata_display.set_json(metadata)

        except Exception as e:
            logger.error(f"Error updating session view: {e}")

    def clear(self) -> None:
        """Clear all displayed data."""
        try:
            self.info_panel.clear()
            self.original_request.clear()
            self.modified_request.clear()
            self.json_display.clear()
            self.stream_display.clear()
            self.metadata_display.clear()
        except Exception as e:
            logger.error(f"Error clearing session view: {e}")

    def grid(self, **kwargs) -> None:
        """
        Grid the frame using provided arguments.

        Args:
            **kwargs: Grid configuration options
        """
        self.frame.grid(**kwargs)
