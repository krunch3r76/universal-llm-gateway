"""
View Layer - Presentation

This package handles all UI components and presentation logic:
- Main window and layout management
- Three-panel view for original/modified/response
- Reusable UI components
"""

from .main_window import MainWindow
from .three_panel_view import ThreePanelView

__all__ = ["MainWindow", "ThreePanelView"]
