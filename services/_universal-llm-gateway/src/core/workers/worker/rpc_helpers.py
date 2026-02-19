"""Helper methods for RPC handlers.

Provides utility functions for token counting and path resolution.
"""

import os
from datetime import datetime
from typing import Any

from universal_logging import format_json_for_log, get_logger
from universal_protocol.errors import EngineError

logger = get_logger(__name__)


class RPCHelpers:
    """Mix-in class providing RPC helper methods."""

    # Assumes self.engine, self.model_config, self.model_loaded, self.model_id exist

    def _resolve_model_path(self, original_path: str) -> str:
        """
        Resolve model path by joining relative paths with MODEL_PATH_ROOT.

        For backwards compatibility, strips legacy root paths (/mnt/torus/models, ~/.models)
        and replaces them with MODEL_PATH_ROOT.

        Args:
            original_path: Path from model config (relative or absolute)

        Returns:
            Resolved absolute path

        Examples:
            Relative: "deepseek-coder-33B-instruct-AWQ"
            MODEL_PATH_ROOT: "/golem/models"
            Result: "/golem/models/deepseek-coder-33B-instruct-AWQ"

            Legacy absolute: "/mnt/torus/models/model.gguf"
            MODEL_PATH_ROOT: "/golem/models"
            Result: "/golem/models/model.gguf"

            Other absolute: "/custom/path/to/model"
            Result: "/custom/path/to/model" (unchanged)
        """
        if not original_path:
            raise ValueError("Model path is empty")

        # Get MODEL_PATH_ROOT from environment
        model_root = os.getenv("MODEL_PATH_ROOT")
        if not model_root:
            raise ValueError(
                "MODEL_PATH_ROOT environment variable not set. "
                "Cannot resolve relative model path."
            )

        # Handle absolute paths with legacy roots
        if original_path.startswith("/") or original_path.startswith("~"):
            expanded_path = os.path.expanduser(original_path)
            # Strip legacy root paths and replace with MODEL_PATH_ROOT
            legacy_roots = ["/mnt/torus/models", os.path.expanduser("~/.models")]
            for legacy_root in legacy_roots:
                if expanded_path.startswith(legacy_root):
                    relative_path = expanded_path[len(legacy_root) :].lstrip("/")
                    return f"{model_root.rstrip('/')}/{relative_path}"
            # Other absolute path - return as-is
            return expanded_path

        # Relative path - join with model root
        return f"{model_root.rstrip('/')}/{original_path.lstrip('/')}"

    async def _handle_count_tokens(self, command: dict[str, Any]) -> dict[str, Any]:
        """Handle token counting request.

        Also outputs the type of messages (e.g., dict or str) and the contents of messages.
        """
        # Remove import - truncation now automatic

        # Handle both messages and prompt
        messages = command.get("messages", None)
        prompt = command.get("prompt", None)
        context_length = command.get("context_length", None)
        use_cpu = command.get("use_cpu")

        # Validate input
        if messages is None and prompt is None:
            raise EngineError(
                code="INVALID_PARAMS",
                message="Either 'messages' or 'prompt' must be provided for token counting",
            )

        if messages is not None and prompt is not None:
            raise EngineError(
                code="INVALID_PARAMS",
                message="Cannot provide both 'messages' and 'prompt' - use one or the other",
            )

        # Use either messages or prompt
        message_or_prompt = messages if messages is not None else prompt
        input_type = "messages" if messages is not None else "prompt"

        logger.info(
            f"🔧 [worker] Counting tokens for {input_type} (type: {type(message_or_prompt).__name__}, use_cpu: {use_cpu})"
        )
        if input_type == "messages":
            logger.info(
                f"🔧 [worker] Messages content: {format_json_for_log(message_or_prompt)}"  # Unicode + automatic truncation
            )
        else:
            logger.info(
                f"🔧 [worker] Prompt content (first 255 chars): {str(message_or_prompt)[:255]}"
            )

        try:
            token_count = await self._count_tokens_with_tokenizer(
                message_or_prompt, use_cpu, context_length
            )

            # Return spec-compliant response per §2.1
            return {
                "count": token_count,
                "method": "exact",  # "exact" or "estimate" per spec
                # Additional diagnostics in data envelope
                "data": {
                    "confidence": 1.0,
                    "model_id": self.model_id,
                    "timestamp": datetime.now().isoformat(),
                },
            }

        except MemoryError as e:
            logger.error(f"❌ [worker] MemoryError during token counting: {e}")
            raise EngineError(
                code="OOM",
                message=f"GPU memory exhausted during token counting: {str(e)}",
                data={
                    "error_type": "gpu_memory_error",
                    "suggestion": "Try reducing context length",
                },
            )
        except OSError as e:
            if "Cannot allocate memory" in str(e) or "No space left on device" in str(
                e
            ):
                logger.error(
                    f"❌ [worker] OSError - Memory allocation failed during token counting: {e}"
                )
                raise EngineError(
                    code="OOM",
                    message=f"Memory allocation failed during token counting: {str(e)}",
                    data={
                        "error_type": "gpu_memory_error",
                        "suggestion": "Try reducing context length",
                    },
                )
            else:
                logger.error(f"❌ [worker] OSError during token counting: {e}")
                raise EngineError(
                    code="ENGINE_ERROR",
                    message=f"System error during token counting: {str(e)}",
                )
        except RuntimeError as e:
            # Check for CUDA/GPU memory errors
            error_str = str(e).lower()
            if any(
                keyword in error_str
                for keyword in [
                    "cuda out of memory",
                    "out of memory",
                    "cuda oom",
                    "gpu memory",
                    "cuda error",
                    "cuda memory",
                    "insufficient memory",
                    "memory allocation failed",
                ]
            ):
                logger.error(
                    f"❌ [worker] GPU Memory Error detected during token counting: {e}"
                )
                raise EngineError(
                    code="OOM",
                    message=f"GPU memory exhausted during token counting: {str(e)}",
                    data={
                        "error_type": "gpu_memory_error",
                        "suggestion": "Try reducing context length",
                    },
                )
            else:
                logger.error(f"❌ [worker] RuntimeError during token counting: {e}")
                raise EngineError(
                    code="ENGINE_ERROR",
                    message=f"Runtime error during token counting: {str(e)}",
                )
        except Exception as e:
            logger.error(f"❌ [worker] Token counting error: {e}")
            raise EngineError(code="ENGINE_ERROR", message=str(e))

    async def _count_tokens_with_tokenizer(
        self,
        message_or_prompt: list[dict[str, str]] | str,
        use_cpu: bool,
        context_length: int | None = None,
    ) -> int:
        """Count tokens using inference_djinn engine."""
        # Remove import - truncation now automatic

        # Ensure model is loaded and engine is healthy before counting tokens
        if not self.engine or not self.engine.is_loaded():
            raise RuntimeError("Model must be loaded before counting tokens")

        try:
            # Use inference_djinn engine's token counting method
            # The engine should handle both messages and prompt
            result = await self.engine.count_tokens_for_messages(
                message_or_prompt, context_length=context_length, use_cpu=use_cpu
            )

            # Log TokenCountResult for debugging
            # Convert dataclass to dict for truncation
            result_dict = {
                "tokens": result.tokens,
                "method": result.method,
                "success": result.success,
                "error": result.error,
                "time_taken": result.time_taken,
                "formatted_prompt": result.formatted_prompt,
            }
            logger.debug(
                f"🔧 [worker] TokenCountResult:\n{format_json_for_log(result_dict)}"  # Unicode + automatic truncation
            )

            # Handle TokenCountResult object - it's a dataclass with specific attributes
            # TokenCountResult has: tokens, method, success, error, time_taken, formatted_prompt
            if not result.success:
                raise RuntimeError(
                    f"Token counting failed: {result.error or 'Unknown error'}"
                )

            # Extract token count from result - TokenCountResult uses 'tokens' not 'token_count'
            token_count = result.tokens
            if token_count is None:
                logger.error(
                    "❌ [worker] No 'tokens' attribute found in TokenCountResult"
                )
                logger.error(f"❌ [worker] TokenCountResult attributes: {dir(result)}")
                raise RuntimeError("No 'tokens' attribute found in TokenCountResult")

            return token_count

        except Exception as e:
            logger.error(f"❌ [worker] inference_djinn token counting failed: {e}")
            raise RuntimeError(f"inference_djinn token counting failed: {e}")
