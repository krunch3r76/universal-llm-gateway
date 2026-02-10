"""
Three panel view for displaying original request, modified request, and response.

Manages the layout and coordination of the three main data display panels
in the universal_stargate GUI.
"""

import tkinter as tk
from tkinter import ttk

from universal_logging import get_logger

from ..model.data_structures import DisplayData
from .components.info_panel import InfoPanel
from .components.json_display import JsonDisplay
from .components.stream_display import StreamDisplay

logger = get_logger(__name__)


class ThreePanelView:
    """Three-panel layout view component"""

    def __init__(self, parent):
        """
        Initialize three panel view.

        Args:
            parent: Parent tkinter widget
        """
        self.parent = parent
        self.current_response_panel = None
        self.current_request_id = (
            None  # Track current request ID to detect new requests
        )
        self.cached_original_request = None  # Cache request data for streaming chunks
        self.cached_modified_request = None  # Cache request data for streaming chunks
        self.setup_layout()

    def setup_layout(self):
        """Create three-panel layout"""
        # Main container
        main_frame = ttk.Frame(self.parent, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # Configure grid weights for equal columns
        main_frame.columnconfigure(0, weight=1)  # Original
        main_frame.columnconfigure(1, weight=1)  # Modified
        main_frame.columnconfigure(2, weight=1)  # Response
        main_frame.rowconfigure(0, weight=0)  # Info panel
        main_frame.rowconfigure(1, weight=1)  # Main content

        # Info panel at top
        self.info_panel = InfoPanel(main_frame)
        self.info_panel.grid(
            row=0, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=(0, 10)
        )

        # Three main panels
        self.original_panel = JsonDisplay(main_frame, "Original Request", "#f8f9fa")
        self.original_panel.grid(
            row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(0, 5)
        )

        self.modified_panel = JsonDisplay(main_frame, "Modified Request", "#fff3cd")
        self.modified_panel.grid(
            row=1, column=1, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(5, 5)
        )

        # Create response panel container that can switch between JSON and Stream
        self.response_container = ttk.Frame(main_frame)
        self.response_container.grid(
            row=1, column=2, sticky=(tk.W, tk.E, tk.N, tk.S), padx=(5, 0)
        )
        self.response_container.columnconfigure(0, weight=1)
        self.response_container.rowconfigure(0, weight=1)

        # Create both response panels
        self.json_response = JsonDisplay(self.response_container, "Response", "#d4edda")
        self.stream_response = StreamDisplay(
            self.response_container, "Response", "#d4edda"
        )

        # Initially show JSON response panel
        self.json_response.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.current_response_panel = "json"

    def update(self, display_data: DisplayData):
        """
        Update all panels with new data.

        Args:
            display_data: DisplayData object containing formatted data for display
        """
        try:
            # Update info panel
            self.info_panel.update(display_data.event_info)

            # Check if this is a new request (different request_id)
            is_new_request = (
                display_data.request_id
                and display_data.request_id != self.current_request_id
            )

            # Handle request panel updates
            # Update request panels for events with valid request data
            # Streaming chunks should NEVER update request panels
            event_type = display_data.event_info.get("type")
            # logger.info(f"🔍 PANEL: Panel update - Event: {event_type}, Request ID: {display_data.request_id}, Current ID: {self.current_request_id}, Is New: {is_new_request}")

            # Log the request data availability
            original_valid = (
                display_data.original_request
                and display_data.original_request.strip() not in ["", "No data"]
            )
            modified_valid = (
                display_data.modified_request
                and display_data.modified_request.strip() not in ["", "No data"]
            )
            logger.debug(
                f"Request data validity - Original: {original_valid}, Modified: {modified_valid}"
            )

            # For request_info events, only original_request is available
            if event_type == "request_info":
                should_update_panels = (
                    is_new_request
                    and display_data.original_request
                    and display_data.original_request.strip() not in ["", "No data"]
                )
            elif event_type == "pre_processing":
                # For pre_processing, original_request is required, modified_request is optional
                # Allow updates even if not "new" request to show transformations when they complete
                should_update_panels = (
                    display_data.original_request
                    and display_data.original_request.strip() not in ["", "No data"]
                )
            elif event_type == "chat_completion":
                # For chat_completion, both should be available
                should_update_panels = (
                    display_data.original_request
                    and display_data.original_request.strip() not in ["", "No data"]
                    and display_data.modified_request
                    and display_data.modified_request.strip() not in ["", "No data"]
                )
            else:
                should_update_panels = False

            if should_update_panels:
                # Event with valid request data - update panels
                logger.info(
                    f"🔍 PANEL: Updating request panels for new {event_type} event (request_id: {display_data.request_id})"
                )

                # Always update original panel if we have original request data
                if display_data.original_request:
                    self.original_panel.update(display_data.original_request)
                    self.cached_original_request = display_data.original_request

                # Only update modified panel if we have modified request data
                if display_data.modified_request:
                    self.modified_panel.update(display_data.modified_request)
                    self.cached_modified_request = display_data.modified_request
                elif event_type == "request_info":
                    # For request_info events, show "Processing..." in modified panel
                    self.modified_panel.update("Processing...")
                    self.cached_modified_request = "Processing..."

                if display_data.request_id:
                    self.current_request_id = display_data.request_id
            else:
                # Streaming chunks or invalid data - DO NOT touch request panels
                if event_type in ["chat_completion", "request_info"]:
                    logger.warning(
                        f"🔍 PANEL: NOT updating request panels for {event_type} event - Conditions: is_new_request={is_new_request}, original_valid={original_valid}, modified_valid={modified_valid}"
                    )
                else:
                    logger.debug(f"Skipping request panel updates - {event_type} event")

            # Update response panel based on type
            if display_data.response.is_streaming:
                self._switch_to_streaming_response()
                # Pass event ID, original request, and request ID for new request detection
                event_id = display_data.event_info.get("id", None)
                logger.debug(
                    f"ThreePanelView passing request_id: {display_data.request_id}"
                )
                self.stream_response.update(
                    display_data.response,
                    event_id,
                    display_data.original_request,
                    display_data.request_id,
                )
            else:
                self._switch_to_json_response()
                self.json_response.update(display_data.response.formatted_text)

            logger.debug("Updated three panel view")

        except Exception as e:
            logger.error(f"Error updating three panel view: {e}")
            self._show_error(f"Error updating display: {e}")

    def _switch_to_json_response(self):
        """Switch to JSON response panel"""
        if self.current_response_panel != "json":
            self.stream_response.grid_remove()
            self.json_response.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
            self.current_response_panel = "json"

    def _switch_to_streaming_response(self):
        """Switch to streaming response panel"""
        if self.current_response_panel != "stream":
            self.json_response.grid_remove()
            self.stream_response.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
            self.current_response_panel = "stream"

    def _show_error(self, error_msg: str):
        """
        Show error in all panels.

        Args:
            error_msg: Error message to display
        """
        # Update all panels with error message
        self.original_panel.update(f"Error: {error_msg}")
        self.modified_panel.update(f"Error: {error_msg}")

        # Show error in current response panel
        if self.current_response_panel == "json":
            self.json_response.update(f"Error: {error_msg}")
        else:
            # For streaming panel, create an error ParsedResponse
            from ..model.data_structures import ParsedResponse

            error_response = ParsedResponse(
                raw_data={},
                formatted_text=f"Error: {error_msg}",
                response_type="error",
                is_streaming=True,
                error_message=error_msg,
            )
            self.stream_response.update(error_response)

        # Clear info panel or show error info
        self.info_panel.clear()

    def clear(self):
        """Clear all panels"""
        self.info_panel.clear()
        self.original_panel.update("")
        self.modified_panel.update("")
        self.current_request_id = None  # Reset request tracking
        self.cached_original_request = None  # Clear cached request data
        self.cached_modified_request = None  # Clear cached request data

        if self.current_response_panel == "json":
            self.json_response.update("")
        else:
            # Create empty response for streaming panel
            from ..model.data_structures import ParsedResponse

            empty_response = ParsedResponse(
                raw_data={},
                formatted_text="",
                response_type="empty",
                is_streaming=False,
            )
            self.stream_response.update(empty_response)

    def get_current_content(self) -> dict:
        """
        Get current content from all panels.

        Returns:
            Dictionary with current panel contents
        """
        content = {
            "info": self.info_panel.get_info(),
            "original": self.original_panel.get_content(),
            "modified": self.modified_panel.get_content(),
            "response_type": self.current_response_panel,
        }

        if self.current_response_panel == "json":
            content["response"] = self.json_response.get_content()
        else:
            # For streaming response, just indicate it's streaming
            content["response"] = "[Streaming Response - see Stream Response panel]"

        return content
