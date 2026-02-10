#!/usr/bin/env python3
"""
Model Manager CLI - Unified tool for model catalog management.

See scripts/model_manager/ for implementation.
"""

import sys

try:
    from huggingface_hub import HfApi  # noqa: F401
except ImportError:
    print("Error: huggingface_hub not installed. Run: pip install huggingface-hub")
    sys.exit(1)

from model_manager import main

if __name__ == "__main__":
    main()
