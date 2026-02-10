"""
vLLM Engine for inference_djinn.

Supports HuggingFace models using vLLM for high-performance inference.
"""

# Import VLLMEngine from the engine.py file (not the engine/ directory package)
# There's both engine.py and engine/ in this directory, and Python prefers the package.
# We use importlib to explicitly load the engine.py file.
import importlib.util
import sys
from pathlib import Path

# Load engine.py explicitly using importlib
_engine_file = Path(__file__).parent / "engine.py"
_spec = importlib.util.spec_from_file_location(
    "inference_djinn.engines.vllm.engine_module", _engine_file
)
_engine_module = importlib.util.module_from_spec(_spec)
sys.modules["inference_djinn.engines.vllm.engine_module"] = _engine_module
_spec.loader.exec_module(_engine_module)

# Import VLLMEngine from the loaded module
VLLMEngine = _engine_module.VLLMEngine

from .inspector import get_vllm_model_info

__all__ = ["VLLMEngine", "get_vllm_model_info"]
