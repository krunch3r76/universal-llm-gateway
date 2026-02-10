"""
Universal Stargate GUI v2

A monitoring interface for Universal Stargate chat completions with
session management and real-time updates.

Features:
- Session management with memory backend
- Real-time event monitoring
- UnixStreamTransport integration
- Modern tkinter-based UI
"""

from . import controller, model, view
from .controller import AppController

__version__ = "2.0.0"
__all__ = [
    "AppController",
    "model",
    "view",
    "controller",
]
