"""
llama-server binary discovery.

Shared between runtime (LlamaServerManager) and measurement scripts.
"""

import os
import shutil
from pathlib import Path


def find_llama_server() -> str:
    """
    Locate llama-server binary.

    Search order:
        1. LLAMA_SERVER_PATH env var
        2. PATH lookup
        3. ~/.local/bin/llama-server

    Returns:
        Absolute path to llama-server binary

    Raises:
        FileNotFoundError: If binary not found
    """
    # 1. Explicit override
    explicit = os.getenv("LLAMA_SERVER_PATH")
    if explicit:
        path = Path(explicit)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
        raise FileNotFoundError(
            f"LLAMA_SERVER_PATH={explicit} does not exist or is not executable"
        )

    # 2. PATH lookup
    found = shutil.which("llama-server")
    if found:
        return found

    # 3. Common local development path
    local = Path.home() / ".local" / "bin" / "llama-server"
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)

    msg = (
        "llama-server binary not found. Install via:\n"
        + "  Docker: build with --with-llama-server (default)\n"
        + "  Local: cmake --build build --target llama-server && "
        + "cp build/bin/llama-server ~/.local/bin/"
    )
    raise FileNotFoundError(msg)
