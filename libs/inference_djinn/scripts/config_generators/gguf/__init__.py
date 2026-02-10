"""
GGUF Model Configuration Generator Package

Modular, simplified tool for generating GGUF model configurations with
support for GPU, CPU, and hybrid profiles.
"""

from .main import main
from .profiles import BaseProfile, SubProfile, WholeProfile

__all__ = ["main", "BaseProfile", "SubProfile", "WholeProfile"]
__version__ = "2.0.0"
