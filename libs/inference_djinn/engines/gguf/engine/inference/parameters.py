"""
GGUF engine parameter building and validation.

Handles generation parameter normalization, stop list validation,
response_format to GBNF grammar conversion, and sampler scheme enforcement.
"""

import json
from typing import Any

from universal_logging import DEBUG, get_logger

logger = get_logger(__name__)


class GGUFParameterBuilder:
    """Handles parameter building and validation for GGUF inference."""

    def __init__(self, engine_instance: Any):
        """
        Initialize parameter builder with reference to engine instance.

        Args:
            engine_instance: The GGUFEngine instance to operate on
        """
        self.engine = engine_instance

    def _validate_stop_list(self, stop: str | list[str] | None) -> list[str]:
        """
        Validate stop list for template friendliness.
        Warns on broad patterns that might trigger prematurely.

        Args:
            stop: Stop string, list of stop strings, or None

        Returns:
            Normalized list of stop strings
        """
        if stop is None:
            return []

        # Normalize to list
        stop_list = [stop] if isinstance(stop, str) else list(stop)

        # Warn on problematic patterns
        broad_patterns = {
            "\n\n": "double newline (may stop after assistant prefix)",
            "\n": "single newline (very broad, may stop prematurely)",
            " ": "single space (extremely broad, will stop immediately)",
            "": "empty string (will match everything)",
        }

        for pattern in stop_list:
            if pattern in broad_patterns:
                logger.warning(
                    f"⚠️  Stop pattern '{repr(pattern)}' detected: {broad_patterns[pattern]}. "
                    f"This may cause premature generation termination."
                )

        return stop_list

    def _convert_response_format(self, response_format: dict[str, Any]) -> Any | None:
        """Convert OpenAI response_format to llama-cpp-python grammar.

        Args:
            response_format: OpenAI-style response_format dict

        Returns:
            LlamaGrammar instance or None
        """
        fmt_type = response_format.get("type")

        match fmt_type:
            case "json_object":
                # Use llama-cpp-python's built-in JSON grammar
                try:
                    from llama_cpp import LlamaGrammar

                    return LlamaGrammar.from_string(self._get_json_gbnf())
                except ImportError:
                    logger.error("LlamaGrammar not available for json_object mode")
                    return None

            case "json_schema":
                json_schema = response_format.get("json_schema", {})
                schema = json_schema.get("schema")
                if not schema:
                    logger.error("json_schema mode requires schema dict")
                    return None

                try:
                    from llama_cpp.llama_grammar import json_schema_to_gbnf

                    gbnf = json_schema_to_gbnf(json.dumps(schema))
                    from llama_cpp import LlamaGrammar

                    return LlamaGrammar.from_string(gbnf)
                except ImportError:
                    logger.error(
                        "json_schema_to_gbnf not available in this llama-cpp-python version"
                    )
                    return None
                except Exception as e:
                    logger.error(f"Failed to convert json_schema to GBNF: {e}")
                    return None

            case "text" | None:
                return None

            case _:
                logger.warning(f"Unknown response_format type: {fmt_type}")
                return None

    @staticmethod
    def _get_json_gbnf() -> str:
        """Basic JSON GBNF grammar for json_object mode."""
        return r"""
root   ::= object
value  ::= object | array | string | number | ("true" | "false" | "null") ws

object ::=
  "{" ws (
    string ":" ws value
    ("," ws string ":" ws value)*
  )? "}" ws

array  ::=
  "[" ws (
    value
    ("," ws value)*
  )? "]" ws

string ::=
  "\"" (
    [^\\"\x7F\x00-\x1F] |
    "\\" (["\\/bfnrt] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F])
  )* "\"" ws

number ::= ("-"? ([0-9] | [1-9] [0-9]*)) ("." [0-9]+)? (("e" | "E") ("+" | "-")? [0-9]+)? ws

ws ::= ([ \t\n] ws)?
"""

    def build_generation_params(
        self,
        raw_params: dict[str, Any],
        is_streaming: bool = False,
    ) -> dict[str, Any]:
        """
        Build normalized generation parameters with sampler scheme enforcement.

        Args:
            raw_params: Client-provided generation parameters
            is_streaming: Whether this is for streaming generation

        Returns:
            Normalized parameter dict ready for llama-cpp-python

        Note:
            - Client-provided parameters are always respected
            - No defaults are applied to inference parameters
            - Sampler scheme is enforced (no mixing top-p/typical/mirostat)
            - response_format is converted to llama-cpp-python grammar
        """
        params = raw_params.copy()

        # Handle response_format → grammar conversion for llama-cpp-python
        response_format = params.pop("response_format", None)
        if response_format:
            grammar = self._convert_response_format(response_format)
            if grammar:
                params["grammar"] = grammar

        # Validate and normalize stop list
        if "stop" in params:
            params["stop"] = self._validate_stop_list(params["stop"])

        # Handle seed configuration
        # Client seed takes precedence over engine default
        if "seed" not in params and self.engine.default_seed is not None:
            params["seed"] = self.engine.default_seed
            if logger.isEnabledFor(DEBUG):
                logger.debug(f"Using default seed: {self.engine.default_seed}")

        # Apply streaming flag (required by llama-cpp-python API)
        if is_streaming:
            params["stream"] = True
        else:
            params["stream"] = False

        # Log final parameters being sent to engine
        logger.info(
            f"🎛️  ENGINE (GGUF): Final generation parameters for inference: "
            f"{dict(params)}"
        )

        if logger.isEnabledFor(DEBUG):
            logger.debug(f"Built generation params with {len(params)} parameters")

        return params
