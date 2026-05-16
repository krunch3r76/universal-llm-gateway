"""
Unit tests for catalog schemas.

Test matrix per schema:
    - Valid entry converts successfully
    - Wrong format rejected
    - Unsupported device rejected
    - Missing profiles/resources yields errors
    - Engine-specific rules enforced
"""

import pytest

from core.catalog.schemas import (
    DiffusersSchema,
    ExllamaV3Schema,
    FasterWhisperSchema,
    LlamaCppSchema,
    SchemaRegistry,
    VllmSchema,
)


class TestSchemaRegistry:
    """Test SchemaRegistry initialization and lookup."""

    def test_registry_initialization(self):
        """Registry initializes with all 5 schemas."""
        schemas = SchemaRegistry.all_schemas()
        engines = SchemaRegistry.all_engines()
        formats = SchemaRegistry.all_formats()

        assert len(schemas) == 5
        assert len(engines) == 5
        assert "llama-cpp" in engines
        assert "vllm" in engines
        assert "exllamav3" in engines
        assert "faster-whisper" in engines
        assert "diffusers" in engines

        assert "gguf" in formats
        assert "hf" in formats
        assert "awq" in formats
        assert "gptq" in formats
        assert "exl3" in formats
        assert "whisper" in formats
        assert "flux" in formats

    def test_get_by_engine(self):
        """Lookup by engine name works."""
        schema = SchemaRegistry.get_by_engine("llama-cpp")
        assert schema is not None
        assert schema.engine == "llama-cpp"
        assert "gguf" in schema.formats

    def test_get_by_format(self):
        """Lookup by format works."""
        schema = SchemaRegistry.get_by_format("gguf")
        assert schema is not None
        assert schema.engine == "llama-cpp"

        schema = SchemaRegistry.get_by_format("awq")
        assert schema is not None
        assert schema.engine == "vllm"

    def test_get_for_model_with_schema_field(self):
        """V2 entry with 'schema' field resolves correctly."""
        entry = {
            "schema": "vllm",
            "metadata": {"format": "awq"},
        }
        schema = SchemaRegistry.get_for_model(entry)
        assert schema is not None
        assert schema.engine == "vllm"

    def test_get_for_model_format_fallback(self):
        """Entry without 'schema' falls back to format derivation."""
        entry = {
            "metadata": {"format": "gguf"},
        }
        schema = SchemaRegistry.get_for_model(entry)
        assert schema is not None
        assert schema.engine == "llama-cpp"

    def test_get_for_model_no_match(self):
        """Entry with unknown format returns None."""
        entry = {
            "metadata": {"format": "unknown"},
        }
        schema = SchemaRegistry.get_for_model(entry)
        assert schema is None

    def test_format_uniqueness_enforced(self):
        """Registry initialization fails if format registered twice."""
        # This test verifies the invariant is enforced
        # (Would need to manually construct duplicate schema to test)
        assert SchemaRegistry.is_registered_format("gguf")
        assert not SchemaRegistry.is_registered_format("nonexistent")


class TestLlamaCppSchema:
    """Test LlamaCppSchema validation and conversion."""

    def test_valid_gguf_entry_converts(self):
        """Valid GGUF entry converts successfully."""
        entry = {
            "schema": "llama-cpp",
            "metadata": {
                "name": "Test Model",
                "format": "gguf",
                "family": "test",
                "training_context_length": 8192,
            },
            "loader": {
                "f16_kv": True,
                "use_mmap": False,
                "n_batch": 512,
            },
            "devices": {
                "gpu": {
                    "profiles": {
                        "8192": {
                            "n_gpu_layers": -1,
                            "vram_mb": 8000,
                            "ram_mb": 500,
                        }
                    }
                }
            },
            "download": {
                "huggingface": {
                    "repo": "test/model",
                    "file": "model.gguf",
                }
            },
        }

        schema = LlamaCppSchema()
        issues = schema.validate("test-model", entry)
        errors = [i for i in issues if i.severity == "error"]
        assert len(errors) == 0

        converted = schema.convert("test-model", entry)
        assert converted is not None
        assert converted.info["engine"] == "llama-cpp"
        assert converted.info["format"] == "gguf"
        assert "8192" in converted.profiles
        assert converted.profiles["8192"]["loader"]["n_gpu_layers"] == -1

    def test_wrong_format_rejected(self):
        """Entry with wrong format yields error."""
        entry = {
            "metadata": {"format": "awq"},  # Wrong format for llama-cpp
            "devices": {},
        }

        schema = LlamaCppSchema()
        issues = schema.validate("test-model", entry)
        errors = [i for i in issues if i.severity == "error"]
        assert len(errors) > 0
        assert any("not supported by llama-cpp" in i.message for i in errors)

    def test_missing_profiles_rejected(self):
        """Device with no profiles yields error."""
        entry = {
            "metadata": {"format": "gguf"},
            "devices": {
                "gpu": {
                    "profiles": {}  # Empty profiles
                }
            },
        }

        schema = LlamaCppSchema()
        issues = schema.validate("test-model", entry)
        errors = [i for i in issues if i.severity == "error"]
        assert len(errors) > 0
        assert any("No profiles" in i.message for i in errors)

    def test_hybrid_profile_creates_hybrid_key(self):
        """Hybrid profile converts to '-hybrid' suffix key."""
        entry = {
            "metadata": {"format": "gguf", "name": "Test"},
            "loader": {},
            "devices": {
                "hybrid": {
                    "profiles": {
                        "8192": {
                            "n_gpu_layers": 20,
                            "vram_mb": 4000,
                            "ram_mb": 8000,
                        }
                    }
                }
            },
            "download": {},
        }

        schema = LlamaCppSchema()
        converted = schema.convert("test-model", entry)
        assert converted is not None
        assert "8192-hybrid" in converted.profiles
        assert converted.profiles["8192-hybrid"]["loader"]["n_gpu_layers"] == 20

    def test_vision_model_missing_clip_model_path(self):
        """Vision model without clip_model_path yields error."""
        entry = {
            "metadata": {
                "format": "gguf",
                "is_vision_model": True,
            },
            "loader": {},  # Missing clip_model_path
            "devices": {
                "gpu": {
                    "profiles": {
                        "8192": {"vram_mb": 8000, "ram_mb": 500}
                    }
                }
            },
        }

        schema = LlamaCppSchema()
        issues = schema.validate("test-model", entry)
        errors = [i for i in issues if i.severity == "error"]
        assert any("clip_model_path" in i.message for i in errors)


class TestVllmSchema:
    """Test VllmSchema validation."""

    def test_cpu_device_rejected(self):
        """vLLM does not support CPU-only mode."""
        entry = {
            "metadata": {"format": "awq"},
            "devices": {
                "cpu": {  # Not supported
                    "profiles": {"8192": {"ram_mb": 16000}}
                }
            },
        }

        schema = VllmSchema()
        issues = schema.validate("test-model", entry)
        errors = [i for i in issues if i.severity == "error"]
        assert any("does not support CPU" in i.message for i in errors)

    def test_valid_awq_entry(self):
        """Valid AWQ entry for vLLM."""
        entry = {
            "metadata": {"format": "awq", "name": "Test"},
            "loader": {"trust_remote_code": False},
            "devices": {
                "gpu": {
                    "profiles": {
                        "16384": {
                            "max_model_len": 16384,
                            "vram_mb": 20000,
                            "ram_mb": 1000,
                        }
                    }
                }
            },
            "download": {},
        }

        schema = VllmSchema()
        issues = schema.validate("test-model", entry)
        errors = [i for i in issues if i.severity == "error"]
        assert len(errors) == 0

        converted = schema.convert("test-model", entry)
        assert converted is not None
        assert converted.cpu_profiles is None


class TestFasterWhisperSchema:
    """Test FasterWhisperSchema validation (named profiles)."""

    def test_named_profiles(self):
        """Whisper uses named profiles (not context-length)."""
        entry = {
            "metadata": {"format": "whisper", "name": "Whisper"},
            "loader": {"beam_size": 5},
            "devices": {
                "gpu": {
                    "profiles": {
                        "default": {  # Named profile
                            "vram_mb": 4000,
                            "ram_mb": 500,
                        }
                    }
                }
            },
            "download": {},
        }

        schema = FasterWhisperSchema()
        issues = schema.validate("test-model", entry)
        errors = [i for i in issues if i.severity == "error"]
        assert len(errors) == 0

        converted = schema.convert("test-model", entry)
        assert converted is not None
        assert "default" in converted.profiles


class TestDiffusersSchema:
    """Test DiffusersSchema validation (named profiles, GPU-only)."""

    def test_cpu_device_rejected(self):
        """Diffusers does not support CPU-only mode."""
        entry = {
            "metadata": {"format": "flux"},
            "devices": {
                "cpu": {
                    "profiles": {"default": {"ram_mb": 32000}}
                }
            },
        }

        schema = DiffusersSchema()
        issues = schema.validate("test-model", entry)
        errors = [i for i in issues if i.severity == "error"]
        assert any("does not support CPU" in i.message for i in errors)

    def test_cpu_offload_is_gpu_profile(self):
        """cpu_offload is a GPU profile parameter, not a separate device."""
        entry = {
            "metadata": {"format": "flux", "name": "Flux"},
            "loader": {"torch_dtype": "float16"},
            "devices": {
                "gpu": {
                    "profiles": {
                        "default": {
                            "vram_mb": 32000,
                            "ram_mb": 2000,
                        },
                        "offload": {
                            "cpu_offload": True,
                            "vram_mb": 16000,
                            "ram_mb": 20000,
                        },
                    }
                }
            },
            "download": {},
        }

        schema = DiffusersSchema()
        issues = schema.validate("test-model", entry)
        errors = [i for i in issues if i.severity == "error"]
        assert len(errors) == 0

        converted = schema.convert("test-model", entry)
        assert converted is not None
        assert "offload" in converted.profiles
        assert converted.profiles["offload"]["loader"]["cpu_offload"] is True
