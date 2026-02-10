"""
Simplified Universal Stargate GUI Package

A clean MVC-based GUI for monitoring universal_stargate transformations.
Replaces the old complex plugin-based system with a simple, extensible architecture.
"""

__version__ = "1.0.0"
__author__ = "Universal LLM Gateway Team"

# Main components
from .main import StargateGUI

__all__ = ["StargateGUI"]
