"""
YAML configuration schemas - Source of Truth for model_loaders.yaml

Complete field coverage including optional/rarely-used fields.
These schemas define what fields are valid, their types, and constraints.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# === Common Schemas ===


class OpenAPIFields(BaseModel):
    """OpenAI API compatibility fields"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="OpenAI-compatible model ID")
    object: str = Field(default="model", description="Object type")
    owned_by: str = Field(..., description="Owner identifier")
    permission: list[str] = Field(
        default_factory=lambda: ["generate"], description="Permissions"
    )


class ResourceConfig(BaseModel):
    """Resource requirements"""

    model_config = ConfigDict(extra="forbid")

    ram_mb: int | None = Field(None, description="RAM requirement in MB")
    vram_mb: int | None = Field(None, description="VRAM requirement in MB")


# === GGUF Schemas ===


class GGUFModelInfo(BaseModel):
    """GGUF model info - complete field coverage"""

    model_config = ConfigDict(extra="forbid")

    # Required Basic Information
    name: str = Field(..., description="Human-readable model name")
    format: Literal["gguf"] = Field(..., description="Model format")
    path: str = Field(..., description="Absolute path to .gguf file")
    enabled: bool = Field(..., description="Whether model is enabled")

    # Required Architecture
    family: str = Field(..., description="Model family (llama, mistral, qwen, etc.)")
    arch: str = Field(..., description="Architecture identifier")

    # Optional Architecture
    quant: str | None = Field(None, description="Quantization method")
    license: str | None = Field(None, description="License type")
    parameters: int | None = Field(None, description="Number of parameters")

    # Optional Training Information
    training_cutoff_year: int | None = Field(
        None, description="Training data cutoff year"
    )
    training_context_length: int | None = Field(
        None, description="Training context length"
    )
    release_date: str | None = Field(None, description="Model release date")

    # Required Capabilities
    supports_chat_history: bool = Field(..., description="Supports chat history")
    input_schema: Literal["prompt", "messages"] = Field(..., description="Input format")

    # Optional Capabilities
    supports_thinking: bool = Field(
        False,
        description=(
            "Model supports extended thinking / chain-of-thought via chat template. "
            'Enables chat_template_kwargs={"enable_thinking": ...} per request.'
        ),
    )

    # Optional Extended Fields (include even if rarely used)
    description: str | None = Field(None, description="Model description")
    capabilities: list[str] | None = Field(None, description="Model capabilities")
    safety_info: dict[str, Any] | None = Field(None, description="Safety information")

    # Optional Default Context Settings
    default_gpu_context: int | None = Field(
        None, description="Default GPU profile context length"
    )
    default_cpu_context: int | None = Field(
        None, description="Default CPU profile context length"
    )

    # Required OpenAI API Compatibility
    openai_api_fields: OpenAPIFields


class GGUFLoaderConfig(BaseModel):
    """GGUF base loader configuration"""

    model_config = ConfigDict(extra="forbid")

    n_batch: int = Field(512, description="Batch size for prompt processing")
    f16_kv: bool = Field(True, description="Use float16 for KV cache")
    use_mmap: bool = Field(True, description="Use memory mapping")
    use_mlock: bool = Field(True, description="Lock pages in RAM")
    verbose: bool = Field(False, description="Enable verbose logging")


class GGUFProfileLoader(BaseModel):
    """GGUF profile loader configuration - can override base_loader settings"""

    model_config = ConfigDict(extra="forbid")

    # Required profile-specific fields
    n_ctx: int = Field(..., description="Context window length")
    n_gpu_layers: int = Field(..., description="GPU layers (-1 = all)")

    # Optional overrides from base_loader (allows profiles to customize per-context)
    n_batch: int | None = Field(
        None, description="Batch size override for this profile"
    )
    f16_kv: bool | None = Field(None, description="Float16 KV cache override")
    use_mmap: bool | None = Field(None, description="Memory mapping override")
    use_mlock: bool | None = Field(None, description="Memory locking override")
    verbose: bool | None = Field(None, description="Verbose logging override")


class GGUFProfile(BaseModel):
    """GGUF context length profile"""

    model_config = ConfigDict(extra="forbid")

    loader: GGUFProfileLoader
    resources: ResourceConfig


class GGUFModelConfig(BaseModel):
    """Complete GGUF model configuration"""

    model_config = ConfigDict(extra="forbid")

    info: GGUFModelInfo
    base_loader: GGUFLoaderConfig
    profiles: dict[str, GGUFProfile] | None = Field(
        None, description="GPU context length profiles"
    )
    cpu_profiles: dict[str, GGUFProfile] | None = Field(
        None, description="CPU-only context length profiles"
    )

    @field_validator("profiles")
    @classmethod
    def validate_profile_keys(cls, v):
        """Ensure profile keys are string representations of integers"""
        if v is not None:
            for key in v.keys():
                if not key.isdigit():
                    raise ValueError(f"Profile key '{key}' must be a string of digits")
        return v

    @field_validator("cpu_profiles")
    @classmethod
    def validate_cpu_profiles(cls, v):
        """Validate CPU profiles have n_gpu_layers: 0"""
        if v is not None:
            # Validate each CPU profile has n_gpu_layers: 0
            for profile_name, profile_config in v.items():
                if not profile_name.isdigit():
                    raise ValueError(
                        f"CPU profile key '{profile_name}' must be a string of digits"
                    )

                # Handle both dict (from YAML) and GGUFProfile (after validation)
                if isinstance(profile_config, dict):
                    loader = profile_config.get("loader", {})
                    n_gpu_layers = (
                        loader.get("n_gpu_layers")
                        if isinstance(loader, dict)
                        else getattr(loader, "n_gpu_layers", None)
                    )
                else:
                    # Already a GGUFProfile object
                    n_gpu_layers = profile_config.loader.n_gpu_layers

                if n_gpu_layers != 0:
                    raise ValueError(
                        f"CPU profile '{profile_name}' must have n_gpu_layers: 0"
                    )
        return v

    @model_validator(mode="after")
    def validate_profiles_present(self):
        """Ensure at least one profile type is present (GPU or CPU)"""
        if not self.profiles and not self.cpu_profiles:
            raise ValueError(
                "At least one of 'profiles' or 'cpu_profiles' must be provided"
            )
        return self

    @model_validator(mode="after")
    def validate_cpu_profiles_format(self):
        """Ensure cpu_profiles only exists for GGUF format"""
        if self.cpu_profiles is not None:
            if self.info.format != "gguf":
                raise ValueError(
                    "cpu_profiles can only be used with GGUF format models"
                )
        return self


# === HF/vLLM Schemas ===


class HFModelInfo(BaseModel):
    """HuggingFace/GPTQ/AWQ model info - complete field coverage"""

    model_config = ConfigDict(extra="forbid")

    # Required Basic Information
    name: str = Field(..., description="Human-readable model name")
    format: Literal["hf", "gptq", "awq"] = Field(..., description="Model format")
    path: str = Field(..., description="Path to model directory")
    enabled: bool = Field(..., description="Whether model is enabled")

    # Required Architecture
    family: str = Field(..., description="Model family")
    arch: str = Field(..., description="Architecture identifier")

    # Optional Architecture
    quant: str | None = Field(None, description="Quantization method")
    license: str | None = Field(None, description="License type")
    parameters: int | None = Field(None, description="Number of parameters")

    # Optional Training Information
    training_cutoff_year: int | None = Field(
        None, description="Training data cutoff year"
    )
    training_context_length: int | None = Field(
        None, description="Training context length"
    )
    release_date: str | None = Field(None, description="Model release date")

    # Required Capabilities
    supports_chat_history: bool = Field(..., description="Supports chat history")
    input_schema: Literal["prompt", "messages"] = Field(..., description="Input format")

    # Optional Capabilities
    supports_thinking: bool = Field(
        False,
        description=(
            "Model supports extended thinking / chain-of-thought via chat template. "
            'Enables chat_template_kwargs={"enable_thinking": ...} per request.'
        ),
    )

    # Optional Extended Fields
    description: str | None = Field(None, description="Model description")
    capabilities: list[str] | None = Field(None, description="Model capabilities")
    safety_info: dict[str, Any] | None = Field(None, description="Safety information")

    # Optional Default Context Settings
    default_gpu_context: int | None = Field(
        None, description="Default GPU profile context length"
    )
    default_cpu_context: int | None = Field(
        None, description="Default CPU profile context length"
    )

    # Required OpenAI API Compatibility
    openai_api_fields: OpenAPIFields


class HFLoaderConfig(BaseModel):
    """HuggingFace/vLLM loader configuration.

    All parameters are optional except max_model_len.
    No defaults provided - catalog must be explicit.
    """

    model_config = ConfigDict(extra="forbid")

    trust_remote_code: bool = Field(
        False, description="Execute remote code (SECURITY: always False)"
    )
    gpu_memory_utilization: float | None = Field(
        None, description="GPU memory fraction (0.0-1.0)"
    )
    max_model_len: int = Field(..., description="Maximum sequence length")
    dtype: str | None = Field(None, description="Model data type")
    enforce_eager: bool | None = Field(None, description="Disable CUDA graphs")
    disable_custom_all_reduce: bool = Field(
        True, description="Disable custom all-reduce (stability)"
    )
    disable_log_stats: bool = Field(
        True, description="Disable statistics logging (noise)"
    )
    disable_sliding_window: bool | None = Field(
        None,
        description="Whether to disable sliding window attention. AWQ models with RoPE scaling require this to be false.",
    )


class HFProfileLoader(BaseModel):
    """HuggingFace profile loader configuration"""

    model_config = ConfigDict(extra="forbid")

    max_model_len: int = Field(..., description="Maximum sequence length")


class HFProfile(BaseModel):
    """HuggingFace context length profile"""

    model_config = ConfigDict(extra="forbid")

    loader: HFProfileLoader
    resources: ResourceConfig = Field(
        ..., description="Resource requirements for this profile"
    )


class HFModelConfig(BaseModel):
    """Complete HuggingFace model configuration"""

    model_config = ConfigDict(extra="forbid")

    info: HFModelInfo
    base_loader: HFLoaderConfig
    profiles: dict[str, HFProfile] = Field(..., description="Context length profiles")

    @field_validator("profiles")
    @classmethod
    def validate_profile_keys(cls, v):
        """Ensure profile keys are string representations of integers"""
        if v is not None:
            for key in v.keys():
                if not key.isdigit():
                    raise ValueError(f"Profile key '{key}' must be a string of digits")
        return v


# === Top-Level Config ===


class ResourceManagement(BaseModel):
    """Resource management settings"""

    model_config = ConfigDict(extra="forbid")

    max_concurrent_cpu_models: int = Field(
        50, description="Maximum concurrent CPU-only models"
    )
    max_concurrent_gpu_models: int = Field(
        10, description="Maximum concurrent GPU models"
    )
    max_concurrent_models: int = Field(
        100,
        description="Maximum concurrent models (fallback for backward compatibility)",
    )


class ModelLoadersConfig(BaseModel):
    """Complete model_loaders.yaml structure"""

    model_config = ConfigDict(extra="forbid")

    resource_management: ResourceManagement
    models: dict[str, Any]  # Union of GGUFModelConfig | HFModelConfig
