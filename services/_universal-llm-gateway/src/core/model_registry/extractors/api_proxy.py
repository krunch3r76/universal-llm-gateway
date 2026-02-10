"""API proxy metadata extraction"""

from typing import Any

from universal_logging import get_logger

from .base import BaseMetadataExtractor

logger = get_logger(__name__)


class APIMetadataExtractor(BaseMetadataExtractor):
    """Extract metadata from API proxy models"""

    def extract(
        self, model_path: str, model_data: dict[str, Any]
    ) -> tuple[int | None, str | None, bool]:
        """Extract metadata from API proxy configuration"""
        try:
            # For API proxy models, metadata comes from the configuration
            loader_config = model_data.get("loader_config", {})

            # Extract context length from loader config
            context_length = loader_config.get("n_ctx")
            if context_length:
                try:
                    context_length = int(context_length)
                except (ValueError, TypeError):
                    context_length = None

            # API proxy models typically support chat templates
            # The actual template is handled by the API provider
            chat_template = None  # Not stored locally for API models
            supports_system = True  # Most API models support system messages

            logger.info(
                f"API proxy metadata extracted - context_length: {context_length}"
            )

            return context_length, chat_template, supports_system

        except Exception as e:
            logger.error(f"Error extracting API proxy metadata: {e}")
            return None, None, False
