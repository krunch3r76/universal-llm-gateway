"""
Inference engines package for inference_djinn.

Provides modular inference engines for different model formats:
- GGUF: llama-cpp-python based engine for GGUF quantized models
- vLLM: High-performance engine for HuggingFace, AWQ, and GPTQ models
- EXL3: Dedicated ExLlamaV3 engine for EXL3 format

Note: AWQ and GPTQ models should use VLLMEngine directly.
Legacy AWQEngine/GPTQEngine aliases have been removed.

This package uses lazy loading - engines are only imported when requested.
"""

from .base import BaseEngine


# Lazy loading implementation
class LazyEngine:
    """Lazy loading wrapper for inference engines"""

    def __init__(self, engine_name: str, import_path: str):
        self.engine_name = engine_name
        self.import_path = import_path
        self._engine_class = None

    def _load_engine_class(self):
        """Load the engine class using absolute import paths"""
        module_path, class_name = self.import_path.rsplit(".", 1)
        # Convert relative paths (engines.xxx) to absolute paths (inference_djinn.engines.xxx)
        if module_path.startswith("engines."):
            module_path = "inference_djinn." + module_path
        module = __import__(module_path, fromlist=[class_name], level=0)
        self._engine_class = getattr(module, class_name)

    def __call__(self, *args, **kwargs):
        """Make the lazy engine callable for instantiation"""
        if self._engine_class is None:
            self._load_engine_class()
        return self._engine_class(*args, **kwargs)

    def __getattr__(self, name):
        """Delegate attribute access to the actual engine class"""
        if self._engine_class is None:
            self._load_engine_class()
        return getattr(self._engine_class, name)


class LazyInspector:
    """Lazy loading wrapper for inspectors"""

    def __init__(self, inspector_name: str, import_path: str):
        self.inspector_name = inspector_name
        self.import_path = import_path
        self._inspector_module = None

    def __getattr__(self, name):
        if self._inspector_module is None:
            # Convert relative paths (engines.xxx) to absolute paths (inference_djinn.engines.xxx)
            import_path = self.import_path
            if import_path.startswith("engines."):
                import_path = "inference_djinn." + import_path
            # Import the inspector module only when first accessed
            self._inspector_module = __import__(import_path, fromlist=["*"], level=0)
        return getattr(self._inspector_module, name)


# Create lazy loading instances
GGUFEngine = LazyEngine("GGUFEngine", "engines.gguf.engine.engine.GGUFEngine")
VLLMEngine = LazyEngine("VLLMEngine", "engines.vllm.engine.VLLMEngine")
ExLlamaV3Engine = LazyEngine("ExLlamaV3Engine", "engines.exl3.engine.ExLlamaV3Engine")
WhisperEngine = LazyEngine("WhisperEngine", "engines.whisper.engine.engine.WhisperEngine")
CrossEncoderEngine = LazyEngine("CrossEncoderEngine", "engines.cross_encoder.engine.CrossEncoderEngine")

# Create lazy loading inspectors
gguf_inspector = LazyInspector("gguf_inspector", "engines.gguf.inspector")
exl3_inspector = LazyInspector("exl3_inspector", "engines.exl3.inspector")
vllm_inspector = LazyInspector("vllm_inspector", "engines.vllm.inspector")

__all__ = [
    "BaseEngine",
    "GGUFEngine",
    "VLLMEngine",
    "ExLlamaV3Engine",
    "WhisperEngine",
    "CrossEncoderEngine",
    "gguf_inspector",
    "exl3_inspector",
    "vllm_inspector",
]


def __getattr__(name):
    """Provide helpful error messages for removed engine aliases."""
    if name == "AWQEngine":
        raise AttributeError(
            "AWQEngine has been removed. Use VLLMEngine for AWQ models instead.\n"
            "Example: from engines import VLLMEngine\n"
            "         engine = VLLMEngine('/path/to/awq/model')\n"
            "See docs/VLLM_ENGINE.md for details."
        )
    elif name == "GPTQEngine":
        raise AttributeError(
            "GPTQEngine has been removed. Use VLLMEngine for GPTQ models instead.\n"
            "Example: from engines import VLLMEngine\n"
            "         engine = VLLMEngine('/path/to/gptq/model')\n"
            "See docs/VLLM_ENGINE.md for details."
        )
    elif name == "ExLlamaV2Engine":
        raise AttributeError(
            "ExLlamaV2Engine has been removed. Use VLLMEngine for GPTQ/EXL2 models instead.\n"
            "Example: from engines import VLLMEngine\n"
            "         engine = VLLMEngine('/path/to/gptq/model')\n"
            "See docs/VLLM_ENGINE.md for details."
        )
    raise AttributeError(f"module 'engines' has no attribute '{name}'")
