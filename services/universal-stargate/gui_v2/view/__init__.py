"""
View layer for Universal Stargate GUI v2.

Components:
- MainWindow: Primary application window
- SessionListView: Session navigation panel
- SessionDetailView: Detailed session display
- Components: Reusable UI components
"""

from . import components
from .main_window import MainWindow
from .session_list import SessionListView
from .session_view import SessionDetailView

__all__ = [
    "MainWindow",
    "SessionListView",
    "SessionDetailView",
    "components",
]
