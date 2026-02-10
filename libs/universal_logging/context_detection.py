"""
Context Detection Module

Automatically detects service name, workspace root, and runtime environment
for auto-initializing logging configuration.
"""

import os
import sys
from pathlib import Path


def detect_service_name() -> str:
    """
    Auto-detect service name from various sources.

    Priority order:
    1. SERVICE_NAME environment variable
    2. __main__ module path inspection
    3. Script name from sys.argv
    4. Fallback to 'unknown'

    Returns:
        Service name string
    """
    # Check environment variable first
    service_name = os.getenv("SERVICE_NAME")
    if service_name:
        return service_name

    # Try to get from __main__ module path
    try:
        main_module = sys.modules.get("__main__")
        if main_module and hasattr(main_module, "__file__") and main_module.__file__:
            main_path = Path(main_module.__file__)

            # Check if running from a service directory
            parts = main_path.parts
            if "services" in parts:
                service_idx = parts.index("services")
                if service_idx + 1 < len(parts):
                    # Get service name from directory after 'services/'
                    return parts[service_idx + 1]

            # Fall back to script name without extension
            return main_path.stem
    except (AttributeError, ValueError, IndexError):
        pass

    # Try sys.argv for script name
    if sys.argv and len(sys.argv) > 0:
        script_path = Path(sys.argv[0])
        if script_path.stem and script_path.stem not in ["python", "python3", "-c"]:
            return script_path.stem

    # Final fallback
    return "unknown"


def detect_workspace_root() -> Path:
    """
    Auto-detect workspace root directory.

    Priority order:
    1. WORKSPACE_ROOT environment variable
    2. Git repository root (if in a git repo)
    3. Directory containing 'libs' or 'services' subdirectories
    4. Current working directory

    Returns:
        Path to workspace root
    """
    # Check environment variable first
    workspace_root = os.getenv("WORKSPACE_ROOT")
    if workspace_root:
        return Path(workspace_root)

    # Try to find git root
    try:
        current = Path.cwd()
        for parent in [current] + list(current.parents):
            if (parent / ".git").exists():
                return parent
    except Exception:
        pass

    # Look for characteristic directories (libs, services)
    try:
        current = Path.cwd()
        for parent in [current] + list(current.parents):
            if (parent / "libs").exists() or (parent / "services").exists():
                return parent
    except Exception:
        pass

    # Fall back to current working directory
    return Path.cwd()


def detect_environment() -> str:
    """
    Detect runtime environment (development, staging, production).

    Priority order:
    1. ENVIRONMENT environment variable
    2. ENV environment variable
    3. Detection based on common patterns
    4. Fallback to 'development'

    Returns:
        Environment name string
    """
    # Check standard environment variables
    env = os.getenv("ENVIRONMENT") or os.getenv("ENV")
    if env:
        return env.lower()

    # Check for production indicators
    if os.getenv("PRODUCTION") or os.getenv("PROD"):
        return "production"

    # Check for staging indicators
    if os.getenv("STAGING") or os.getenv("STAGE"):
        return "staging"

    # Default to development
    return "development"
