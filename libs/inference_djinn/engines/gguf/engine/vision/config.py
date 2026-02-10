"""Configuration schema for vision models."""

from dataclasses import dataclass, field
from pathlib import Path

from .registry import is_vision_model, list_supported_vision_models


@dataclass
class VisionConfig:
    """Configuration for vision/multi-modal model loading."""

    # Required for vision models
    clip_model_path: str | None = None  # Path to mmproj/clip model
    vision_architecture: str | None = None  # Key into VISION_MODEL_REGISTRY

    # Optional overrides
    n_ctx_override: int | None = None  # Override default context size
    tokens_per_image: int | None = None  # Override registry default

    # Derived/computed (set during validation)
    is_vision_model: bool = field(default=False, init=False)

    def validate(self) -> list[str]:
        """Validate configuration, return list of errors."""
        errors = []

        if self.vision_architecture:
            if not is_vision_model(self.vision_architecture):
                supported = ", ".join(list_supported_vision_models())
                errors.append(
                    f"Unknown vision architecture: {self.vision_architecture}. "
                    f"Supported: {supported}"
                )

            if not self.clip_model_path:
                errors.append("clip_model_path required for vision models")
            elif not Path(self.clip_model_path).exists():
                errors.append(f"CLIP model not found: {self.clip_model_path}")

            # Validate tokens_per_image if provided
            if self.tokens_per_image is not None and self.tokens_per_image <= 0:
                errors.append(
                    f"tokens_per_image must be > 0, got: {self.tokens_per_image}"
                )

            if not errors:
                self.is_vision_model = True

        return errors

    def validate_or_raise(self) -> None:
        """Validate configuration, raise ValueError on first error."""
        errors = self.validate()
        if errors:
            raise ValueError(f"Vision config validation failed: {errors[0]}")

    @classmethod
    def from_kwargs(cls, kwargs: dict) -> "VisionConfig":
        """Extract vision config from model kwargs."""
        return cls(
            clip_model_path=kwargs.pop("clip_model_path", None),
            vision_architecture=kwargs.pop("vision_architecture", None),
            n_ctx_override=kwargs.pop("vision_n_ctx", None),
            tokens_per_image=kwargs.pop("tokens_per_image", None),
        )
