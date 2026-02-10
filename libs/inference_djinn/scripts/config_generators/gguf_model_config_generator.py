#!/usr/bin/env python3
"""
GGUF Model Configuration Generator (Modularized)

Thin wrapper that delegates to the modularized gguf_config_generator package.

Usage:
    python scripts/gguf_model_config_generator.py /path/to/model.gguf
    python scripts/gguf_model_config_generator.py /path/to/model.gguf --cpu-only
    python scripts/gguf_model_config_generator.py /path/to/model.gguf --use-cached --push
"""

import sys
from pathlib import Path


def check_llama_cpp_availability():
    """Check if llama_cpp is available and exit if not found."""
    try:
        import llama_cpp

        return True
    except ImportError:
        print("Error: llama_cpp is not available.", file=sys.stderr)
        print("", file=sys.stderr)
        print(
            "The GGUF configuration generator requires llama-cpp-python to function properly.",
            file=sys.stderr,
        )
        print(
            "All model testing and resource measurement depends on this library.",
            file=sys.stderr,
        )
        print("", file=sys.stderr)
        print("Please install it with:", file=sys.stderr)
        print("  pip install llama-cpp-python", file=sys.stderr)
        print("", file=sys.stderr)
        print("Or for GPU support:", file=sys.stderr)
        print("  pip install llama-cpp-python[cuda]", file=sys.stderr)
        print("", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    # Check llama_cpp availability before proceeding
    check_llama_cpp_availability()

    # Add the gguf package directory to sys.path for absolute import
    script_dir = Path(__file__).parent
    gguf_dir = script_dir / "gguf"
    if gguf_dir.exists():
        sys.path.insert(0, str(script_dir))
    # Import and run main function
    try:
        # Try relative import first (works when run as module)
        from .gguf import main
    except ImportError:
        # Fall back to absolute import (works when run directly)
        from gguf import main

    main()
