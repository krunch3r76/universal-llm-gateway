"""
ExLlamaV3 engine model loading operations.

Handles model loading, configuration, and unload operations.
"""

import asyncio
from universal_logging import get_logger
from pathlib import Path
from typing import Any

try:
    from exllamav3 import Config, Generator, Model, Tokenizer

    exllamav3_available = True
except ImportError:
    exllamav3_available = False

logger = get_logger(__name__)


class ExLlamaV3ModelLoader:
    """Handles model loading and unloading for ExLlamaV3 engine."""

    def __init__(self, engine_instance: Any):
        """
        Initialize model loader with reference to engine instance.

        Args:
            engine_instance: The ExLlamaV3Engine instance to operate on
        """
        self.engine = engine_instance

    async def load(self) -> None:
        """Load ExLlamaV3 model using provided configuration only"""
        if not exllamav3_available:
            raise RuntimeError(
                "ExLlamaV3 not available. Install with the build script: ./libs/inference_djinn/scripts/build/shell/exlamma/build_exllamav3_blackwell.sh"
            )

        if not Path(self.engine.model_path).exists():
            raise FileNotFoundError(f"Model path not found: {self.engine.model_path}")

        try:
            # Create ExLlamaV3 configuration
            self.engine.config = await asyncio.to_thread(
                Config.from_directory, self.engine.model_path
            )

            # Set configuration attributes (ExLlamaV3 has different parameter names)
            if hasattr(self.engine.config, "max_input_len"):
                self.engine.config.max_input_len = self.engine.max_input_len
            if hasattr(self.engine.config, "max_seq_len"):
                self.engine.config.max_seq_len = self.engine.max_seq_len
            if hasattr(self.engine.config, "max_attention_size"):
                self.engine.config.max_attention_size = self.engine.max_attention_size

            # Add any additional ExLlamaV3-specific parameters
            exllamav3_params = [
                "no_flash_attn",
                "no_graphs",
                "no_sdpa",
                "no_xformers",
                "scale_pos_emb",
                "scale_alpha_value",
                "scale_long_factor",
                "scale_short_factor",
                "chunk_size",
                "progress",
                "lazy",
            ]

            for param in exllamav3_params:
                if param in self.engine.kwargs and hasattr(self.engine.config, param):
                    setattr(self.engine.config, param, self.engine.kwargs[param])

            # Load model using the correct API
            self.engine.model = await asyncio.to_thread(
                Model.from_config, self.engine.config
            )

            # Load the model
            await asyncio.to_thread(
                self.engine.model.load, progressbar=True, verbose=True
            )

            # Load tokenizer
            self.engine.tokenizer = await asyncio.to_thread(
                Tokenizer.from_config, self.engine.config
            )

            # Create generator (ExLlamaV3 has simplified generator creation)
            self.engine.generator = await asyncio.to_thread(
                Generator, model=self.engine.model, tokenizer=self.engine.tokenizer
            )

            self.engine.loaded = True
            print(f"Loaded ExLlamaV3 model: {self.engine.model_path}")
            print(
                f"ExLlamaV3 config: max_seq_len={self.engine.max_seq_len}, EXL3 quantization support"
            )

        except Exception as e:
            raise RuntimeError(f"Failed to load ExLlamaV3 model: {e}")

    async def unload(self) -> None:
        """Unload ExLlamaV3 model and free GPU memory"""
        if self.engine.generator:
            del self.engine.generator
            self.engine.generator = None

        if self.engine.model:
            del self.engine.model
            self.engine.model = None

        if self.engine.tokenizer:
            del self.engine.tokenizer
            self.engine.tokenizer = None

        if self.engine.config:
            del self.engine.config
            self.engine.config = None

        # Clear GPU cache
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass

        self.engine._analysis_cache = None
        self.engine.loaded = False
        print(f"Unloaded ExLlamaV3 model: {self.engine.model_path}")
