"""Synthetic model listing and enumeration with hardware-aware filtering."""

from model_id import ModelId

from src.core.gpu_detection import GPUCapabilities
from src.core.synthetic_models import SyntheticModelResolver
from src.schemas.model_info import ModelInfo


class ListingsMixin:
    """Enumerate synthetic models for HTTP and WebSocket model catalog surfaces."""

    def get_available_synthetic_model_ids(
        self, enabled_only: bool = True, available_only: bool = True
    ) -> list[str]:
        """
        Get list of synthetic model IDs with optional filtering.

        This is the canonical method for getting model IDs - used by both
        HTTP /v1/models endpoint and WebSocket INIT message to ensure
        consistent behavior.

        Filters models based on:
        1. Enabled status (if enabled_only=True)
        2. File availability (if available_only=True)
        3. Hardware capabilities (CPU-only gateways exclude GPU/hybrid models)

        Args:
            enabled_only: Only return enabled models (default: True)
            available_only: Only return models with available file paths (default: True)

        Returns:
            List of synthetic model IDs that pass the filters
        """
        # Get all synthetic models from catalog
        config = self.model_loaders_config

        synthetic_models = SyntheticModelResolver.get_all_synthetic_models(config)

        # Use the same GPU detection as apply_availability_report() so both code
        # paths agree. pynvml (get_vram_info) can return 0 even when a GPU is
        # present (e.g. NVML not accessible inside a container), which would
        # silently exclude all GPU model variants from the INIT message.
        is_cpu_only = not GPUCapabilities.is_hardware_gpu_available()

        # Apply filters
        model_ids = []
        for sm in synthetic_models:
            # Filter by enabled status
            if enabled_only:
                enabled = self.is_model_enabled(sm.synthetic_id)
                if not enabled:
                    continue

            # Filter by file availability
            if available_only:
                path_available = self.is_model_path_available(sm.synthetic_id)
                if not path_available:
                    continue

            # Filter by hardware capabilities
            # CPU-only gateways (vram_total_mb == 0) can only run CPU models
            # GPU and hybrid models require GPU support
            if is_cpu_only:
                try:
                    parsed = ModelId.parse(sm.synthetic_id)
                    # Only include CPU models (-cpu suffix)
                    # Exclude GPU models (no suffix) and hybrid models (-hybrid suffix)
                    if not parsed.is_cpu:
                        continue
                except ValueError:
                    # Not a synthetic ID, include as-is (pipeline IDs, etc.)
                    pass

            model_ids.append(sm.synthetic_id)

        return model_ids

    def list_models(self, enabled_only: bool = False) -> list[ModelInfo]:
        """
        List all synthetic models or only enabled models.

        Returns synthetic model IDs with explicit context lengths.
        """
        # Get all synthetic models
        all_synthetic_models = SyntheticModelResolver.get_default_synthetic_models(
            self.model_loaders_config
        )

        models = []
        for synthetic_model in all_synthetic_models:
            if enabled_only and not self.is_model_enabled(synthetic_model.synthetic_id):
                continue

            # Create ModelInfo from synthetic model
            model_info = self.get_model_info(synthetic_model.synthetic_id)
            if model_info:
                models.append(model_info)

        return models
