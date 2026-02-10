"""
VLLM engine implementation.

Modularized engine structure for maintainability.

This package contains submodules for the vLLM engine. The main VLLMEngine class
is defined in the parent directory's engine.py file. This __init__.py re-exports
it to allow imports like: from inference_djinn.engines.vllm.engine import VLLMEngine
"""

# Re-export VLLMEngine from the parent module's engine.py
# This resolves the namespace conflict where both engine.py and engine/ exist
import importlib.util
import sys
from pathlib import Path

# Load the parent's engine.py file explicitly
_parent_dir = Path(__file__).parent.parent
_engine_file = _parent_dir / "engine.py"
_spec = importlib.util.spec_from_file_location(
    "inference_djinn.engines.vllm.engine_file", _engine_file
)
_engine_module = importlib.util.module_from_spec(_spec)
sys.modules["inference_djinn.engines.vllm.engine_file"] = _engine_module
_spec.loader.exec_module(_engine_module)

# Re-export VLLMEngine
VLLMEngine = _engine_module.VLLMEngine

__all__ = ["VLLMEngine"]
