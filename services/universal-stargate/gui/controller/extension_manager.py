"""
Extension manager for universal_stargate GUI extensibility.

Provides hooks for extending the GUI functionality without modifying core code.
Simplified version that focuses on data processing extensions.
"""

from collections.abc import Callable
from typing import Any

from universal_logging import get_logger

from ..model.data_structures import DisplayData, StargateEvent

logger = get_logger(__name__)


class ExtensionManager:
    """Manages extensions for processing and display"""

    def __init__(self):
        """Initialize extension manager with empty hook lists"""
        self.pre_processors: list[Callable[[StargateEvent], StargateEvent]] = []
        self.post_processors: list[Callable[[DisplayData], DisplayData]] = []
        self.view_extensions: list[Any] = []

    def register_pre_processor(
        self, processor: Callable[[StargateEvent], StargateEvent]
    ):
        """
        Register pre-processing extension.

        Args:
            processor: Function that takes and returns a StargateEvent
        """
        self.pre_processors.append(processor)
        logger.info(f"Registered pre-processor: {processor.__name__}")

    def register_post_processor(self, processor: Callable[[DisplayData], DisplayData]):
        """
        Register post-processing extension.

        Args:
            processor: Function that takes and returns DisplayData
        """
        self.post_processors.append(processor)
        logger.info(f"Registered post-processor: {processor.__name__}")

    def register_view_extension(self, extension: Any):
        """
        Register view extension (for future use).

        Args:
            extension: View extension object
        """
        self.view_extensions.append(extension)
        logger.info(f"Registered view extension: {type(extension).__name__}")

    def apply_pre_process(self, event: StargateEvent) -> StargateEvent:
        """
        Apply all pre-processors to an event.

        Args:
            event: StargateEvent to process

        Returns:
            Processed StargateEvent
        """
        processed_event = event

        for processor in self.pre_processors:
            try:
                processed_event = processor(processed_event)
            except Exception as e:
                logger.error(f"Error in pre-processor {processor.__name__}: {e}")
                # Continue with unprocessed event on error

        return processed_event

    def apply_post_process(self, display_data: DisplayData) -> DisplayData:
        """
        Apply all post-processors to display data.

        Args:
            display_data: DisplayData to process

        Returns:
            Processed DisplayData
        """
        processed_data = display_data

        for processor in self.post_processors:
            try:
                processed_data = processor(processed_data)
            except Exception as e:
                logger.error(f"Error in post-processor {processor.__name__}: {e}")
                # Continue with unprocessed data on error

        return processed_data

    def get_extension_count(self) -> dict:
        """
        Get count of registered extensions.

        Returns:
            Dictionary with extension counts
        """
        return {
            "pre_processors": len(self.pre_processors),
            "post_processors": len(self.post_processors),
            "view_extensions": len(self.view_extensions),
        }

    def clear_extensions(self):
        """Clear all registered extensions"""
        self.pre_processors.clear()
        self.post_processors.clear()
        self.view_extensions.clear()
        logger.info("Cleared all extensions")
