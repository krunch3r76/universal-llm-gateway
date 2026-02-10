"""
Controller Layer - Business Logic

This package handles business logic and coordination between model and view:
- Event processing and transformation
- Data processing and formatting
- Extension management
"""

from .data_processor import DataProcessor
from .event_controller import EventController
from .extension_manager import ExtensionManager

__all__ = ["EventController", "DataProcessor", "ExtensionManager"]
