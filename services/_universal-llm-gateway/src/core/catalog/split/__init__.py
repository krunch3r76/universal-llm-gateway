"""
Catalog split utilities for domain-based file operations.

Provides path mapping and atomic file writing for individual model files.
"""

from .mapping import determine_model_path
from .writer import write_model_file

__all__ = ["determine_model_path", "write_model_file"]
