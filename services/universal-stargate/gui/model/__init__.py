"""
Model Layer - Data Management

This package handles all data-related operations including:
- Unix socket communication
- Data structures and parsing
- Response parsing and formatting
"""

from .data_structures import DisplayData, ParsedResponse, StargateEvent
from .response_parser import ResponseParser

__all__ = ["StargateEvent", "ParsedResponse", "DisplayData", "ResponseParser"]
