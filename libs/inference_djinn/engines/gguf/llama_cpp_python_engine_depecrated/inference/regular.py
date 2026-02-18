"""
GGUF engine regular (non-streaming) inference operations.

Handles non-streaming completion generation for both chat and prompt inputs.
Supports multi-modal (vision) messages via MessageList type.
"""

import asyncio
import contextlib
import time
from collections.abc import Iterator
from functools import partial
from typing import Any

from universal_logging import get_logger

from ..vision.types import MessageList
from .abort_controller import AbortController
from .parent_monitor import monitor_parent_death

logger = get_logger(__name__)


class GGUFRegularInference:
    """Handles regular (non-streaming) inference for GGUF engine."""

    def __init__(self, engine_instance: Any):
        """Initialize regular inference with reference to engine instance."""
        self.engine = engine_instance

    async def _create_completion_unified(
        self,
        messages: MessageList | None,
        prompt: str | None,
        generation_params: dict[str, Any],
        is_streaming: bool = False,
    ) -> dict[str, Any] | Iterator[dict[str, Any]]:
        """Unified completion call for both streaming and non-streaming."""
        try:
            llama_params = generation_params.copy()
            llama_params["stream"] = bool(is_streaming)

            if self.engine.debug_token_level:
                try:
                    if prompt:
                        token_count = len(
                            self.engine.llama_model.tokenize(prompt.encode("utf-8"))
                        )
                        logger.debug(f"Prompt token count: {token_count}")
                        # Debug: show first 32 tokens for non-streaming
                        if not is_streaming:
                            tokens = self.engine.llama_model.tokenize(
                                prompt.encode("utf-8")
                            )
                            first_32_tokens = (
                                tokens[:32] if len(tokens) >= 32 else tokens
                            )
                            logger.debug(
                                f"First {len(first_32_tokens)} token IDs: {first_32_tokens}"
                            )
                    elif messages:
                        logger.debug(f"Messages count: {len(messages)}")
                except Exception as debug_error:  # pragma: no cover - debug only
                    logger.debug(f"Token debugging failed: {debug_error}")

            loop = asyncio.get_running_loop()
            if messages is not None:
                blocking_call = partial(
                    self.engine.llama_model.create_chat_completion,
                    messages=messages,
                    **llama_params,
                )
            else:
                blocking_call = partial(
                    self.engine.llama_model.create_completion,
                    prompt=prompt,
                    **llama_params,
                )

            # Log before native call (crash diagnostic: if this is last log, crash is in native code)
            has_response_format = "response_format" in llama_params
            has_grammar = "grammar" in llama_params
            logger.info(
                f"🔧 Calling llama.cpp (messages={messages is not None}, "
                f"response_format={has_response_format}, grammar={has_grammar})"
            )

            result = await loop.run_in_executor(None, blocking_call)
            logger.info("✅ llama.cpp call completed successfully")
            return result
        except Exception as e:  # pragma: no cover - passthrough
            error_msg = str(e)
            logger.error(f"Unified completion failed: {error_msg}")
            raise RuntimeError(f"Unified completion failed: {error_msg}") from e

    def _extract_llama_params(
        self, generation_params: dict[str, Any]
    ) -> dict[str, Any]:
        """Extract parameters for llama-cpp-python - NO DEFAULTS APPLIED"""
        llama_params = {}

        param_mapping = {
            "max_tokens": "max_tokens",
            "temperature": "temperature",
            "top_p": "top_p",
            "top_k": "top_k",
            "stop": "stop",
            "repeat_penalty": "repeat_penalty",
            "presence_penalty": "presence_penalty",
            "frequency_penalty": "frequency_penalty",
        }

        for param_key, llama_key in param_mapping.items():
            if param_key in generation_params:
                llama_params[llama_key] = generation_params[param_key]

        if "stop" in llama_params:
            stop_sequences = llama_params["stop"]
            if isinstance(stop_sequences, str):
                llama_params["stop"] = [stop_sequences]
            elif stop_sequences is None:
                llama_params["stop"] = []

        return llama_params

    async def generate(
        self, data: dict[str, Any], cancellation_event: asyncio.Event | None = None
    ) -> dict[str, Any]:
        """Generate non-streaming response with optional cancellation."""
        if not self.engine.loaded:
            raise RuntimeError("Model not loaded")

        abort_controller = AbortController(self.engine.llama_model)
        try:
            abort_controller.arm()

            # Cancellation event monitor (optional)
            cancellation_monitor: asyncio.Task[None] | None = None
            if cancellation_event:

                async def monitor_cancellation() -> None:
                    await cancellation_event.wait()
                    abort_controller.trigger()
                    logger.info("🛑 Abort callback triggered - generation will stop")

                cancellation_monitor = asyncio.create_task(monitor_cancellation())

            # Parent death monitor (always enabled)
            parent_monitor: asyncio.Task[None] | None = None
            parent_monitor = asyncio.create_task(
                monitor_parent_death(
                    abort_trigger=abort_controller.trigger,
                    cancellation_event=cancellation_event,
                )
            )

            try:
                generation_params = self.engine._get_generation_params(data)

                prompt = self.engine._extract_prompt(data)
                messages = data.get("messages", []) if prompt is None else None

                logger.info("GGUF inference request:")
                logger.info(f"  Model: {self.engine._get_model_name()}")
                if prompt is not None:
                    logger.info("  Prompt-based request (client-formatted)")
                    logger.info(f"  Prompt length: {len(prompt)} characters")
                    if "chat_format" in self.engine.kwargs:
                        logger.info(
                            "  Ignoring chat_format '%s' for prompt-based request (client handles templating)",
                            self.engine.kwargs["chat_format"],
                        )
                    if self.engine._chat_template_available:
                        logger.info(
                            "  ℹ️  Model supports chat templates but received prompt string. "
                            "Consider using messages format to leverage automatic chat templating."
                        )
                else:
                    logger.info("  Messages-based request (chat template)")
                    logger.info(f"  Messages count: {len(messages)}")
                    if messages:
                        logger.info(
                            "  First message role: %s",
                            messages[0].get("role", "unknown"),
                        )
                        logger.info(
                            "  Last message role: %s",
                            messages[-1].get("role", "unknown"),
                        )

                if generation_params:
                    logger.info("  Generation parameters:")
                    for key, value in generation_params.items():
                        logger.info(f"    {key}: {value}")
                else:
                    logger.info("  Generation parameters: None (using defaults)")

                chat_mode = self.engine.kwargs.get("chat_completion_mode", "modern")
                logger.info(f"  Chat completion mode: {chat_mode}")

                start_time = time.time()

                if chat_mode == "modern":
                    logger.info("  Using unified completion path for non-streaming")

                    await self.engine._perform_warmup(
                        is_streaming=False,
                        messages=messages,
                        prompt=prompt,
                        request_max_tokens=generation_params.get("max_tokens"),
                    )

                    try:
                        final_gen_params = self.engine._build_generation_params(
                            generation_params, is_streaming=False
                        )

                        response = await self._create_completion_unified(
                            messages, prompt, final_gen_params, is_streaming=False
                        )
                        logger.info("  Generation call completed")

                        if isinstance(response, dict) and "choices" in response:
                            choice = response["choices"][0]
                            content = ""
                            finish_reason = "stop"

                            if messages is not None:
                                logger.info("  Parsing chat completion response")
                                if "message" in choice:
                                    content = (
                                        choice["message"].get("content", "").strip()
                                    )
                                    finish_reason = choice.get("finish_reason", "stop")
                                    logger.info(
                                        f"  Response content length: {len(content)} characters"
                                    )
                                    logger.info(f"  Finish reason: {finish_reason}")
                                else:
                                    raise RuntimeError(
                                        f"Unexpected chat completion response format: {choice}"
                                    )
                            else:
                                logger.info("  Parsing prompt completion response")
                                content = choice.get("text", "").strip()
                                finish_reason = choice.get("finish_reason", "stop")
                                logger.info(
                                    f"  Response content length: {len(content)} characters"
                                )
                                logger.info(f"  Finish reason: {finish_reason}")

                            logger.info("  Computing token usage...")
                            prompt_for_counting = (
                                messages if messages is not None else prompt
                            )
                            prompt_result = await self.engine._token_counter.count_tokens_for_messages(
                                prompt_for_counting
                            )
                            completion_result = await self.engine._token_counter.count_tokens_for_messages(
                                content
                            )
                            prompt_tokens: int = prompt_result.tokens
                            completion_tokens: int = completion_result.tokens

                            logger.info(
                                "  Token usage - prompt: %s, completion: %s",
                                prompt_tokens,
                                completion_tokens,
                            )

                            response_data = {
                                "id": f"chatcmpl-{int(time.time() * 1000)}",
                                "object": "chat.completion",
                                "created": int(time.time()),
                                "model": self.engine._get_model_name(),
                                "choices": [
                                    {
                                        "index": 0,
                                        "message": {
                                            "role": "assistant",
                                            "content": content,
                                        },
                                        "finish_reason": finish_reason,
                                    }
                                ],
                                "usage": self.engine._create_usage_stats(
                                    prompt_tokens, completion_tokens
                                ),
                            }
                        else:
                            raise RuntimeError(
                                f"Unexpected response format from llama-cpp: {type(response)}"
                            )

                    except Exception as e:  # pragma: no cover - passthrough
                        error_msg = str(e)
                        logger.error(
                            f"Unified completion failed: {type(e).__name__}: {error_msg}"
                        )
                        raise RuntimeError(
                            f"Unified completion failed: {error_msg}"
                        ) from e

                    if response_data:
                        response_data["generation_time"] = time.time() - start_time
                        response_data["completion_mode"] = "modern"
                    else:
                        logger.error(
                            "Modern completion failed - no fallback to legacy (modern mode enforced)"
                        )
                        raise RuntimeError(
                            "Modern completion failed and legacy fallback disabled"
                        )
                else:
                    raise RuntimeError(
                        "Only modern completion mode is supported; legacy mode is not allowed."
                    )

                return response_data
            finally:
                # Cancel monitors
                if cancellation_monitor is not None:
                    cancellation_monitor.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await cancellation_monitor

                if parent_monitor is not None:
                    parent_monitor.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await parent_monitor
        finally:
            abort_controller.disarm()
