"""
Test suffix handling in synthetic model resolution.

Validates that CPU/hybrid flags are preserved for named-profile models.
"""

import sys
from pathlib import Path

# Add service source to path
sys.path.insert(0, str(Path(__file__).parent.parent / "services/_universal-llm-gateway/src"))

from core.synthetic_models import SyntheticModelResolver


def test_resolve_context_based_models():
    """Test resolution of context-based models with suffixes."""
    # GPU model with context
    result = SyntheticModelResolver.resolve_synthetic_id("qwen2-5-coder-14b-32768")
    assert result == ("qwen2-5-coder-14b", 32768, False, False)
    
    # CPU model with context
    result = SyntheticModelResolver.resolve_synthetic_id("qwen2-5-coder-14b-32768-cpu")
    assert result == ("qwen2-5-coder-14b", 32768, True, False)
    
    # Hybrid model with context
    result = SyntheticModelResolver.resolve_synthetic_id("qwen2-5-coder-14b-32768-hybrid")
    assert result == ("qwen2-5-coder-14b", 32768, False, True)


def test_resolve_named_profile_models():
    """Test resolution of named-profile models (audio, vision)."""
    # Base named-profile model (GPU)
    result = SyntheticModelResolver.resolve_synthetic_id("whisper-large-v3")
    assert result == ("whisper-large-v3", 0, False, False)
    
    # Named-profile CPU model - CRITICAL: must preserve CPU flag
    result = SyntheticModelResolver.resolve_synthetic_id("whisper-large-v3-cpu")
    assert result == ("whisper-large-v3", 0, True, False), \
        "CPU flag must be preserved for named-profile models"
    
    # Named-profile hybrid model (theoretical)
    result = SyntheticModelResolver.resolve_synthetic_id("whisper-large-v3-hybrid")
    assert result == ("whisper-large-v3", 0, False, True), \
        "Hybrid flag must be preserved for named-profile models"


def test_get_model_config_for_named_profile_with_cpu():
    """Test that CPU named-profile models route to cpu_profiles."""
    config = {
        "models": {
            "whisper-large-v3": {
                "info": {"format": "whisper"},
                "profiles": {
                    "default": {"loader": {"model_size": "large-v3", "device": "cuda"}}
                },
                "cpu_profiles": {
                    "default": {"loader": {"model_size": "large-v3", "device": "cpu"}}
                }
            }
        }
    }
    
    # GPU named-profile model should use profiles
    result = SyntheticModelResolver.get_model_config_for_synthetic_id(
        config, "whisper-large-v3"
    )
    assert result is not None
    base_config, profile_config = result
    assert profile_config == {"loader": {"model_size": "large-v3", "device": "cuda"}}
    
    # CPU named-profile model should use cpu_profiles
    result = SyntheticModelResolver.get_model_config_for_synthetic_id(
        config, "whisper-large-v3-cpu"
    )
    assert result is not None
    base_config, profile_config = result
    assert profile_config == {"loader": {"model_size": "large-v3", "device": "cpu"}}, \
        "CPU suffix must route to cpu_profiles for named-profile models"


def test_get_named_profile():
    """Test named profile retrieval with fallbacks."""
    config = {
        "models": {
            "whisper-test": {
                "profiles": {
                    "default": {"loader": {"setting": "default_value"}},
                    "fast": {"loader": {"setting": "fast_value"}},
                    "accurate": {"loader": {"setting": "accurate_value"}}
                }
            }
        }
    }
    
    # Request specific profile
    profile = SyntheticModelResolver.get_named_profile(config, "whisper-test", "fast")
    assert profile == {"loader": {"setting": "fast_value"}}
    
    # Request non-existent profile, should fallback to default
    profile = SyntheticModelResolver.get_named_profile(config, "whisper-test", "nonexistent")
    assert profile == {"loader": {"setting": "default_value"}}
    
    # No profile name specified, should use default
    profile = SyntheticModelResolver.get_named_profile(config, "whisper-test")
    assert profile == {"loader": {"setting": "default_value"}}


if __name__ == "__main__":
    test_resolve_context_based_models()
    print("✓ test_resolve_context_based_models")
    
    test_resolve_named_profile_models()
    print("✓ test_resolve_named_profile_models")
    
    test_get_model_config_for_named_profile_with_cpu()
    print("✓ test_get_model_config_for_named_profile_with_cpu")
    
    test_get_named_profile()
    print("✓ test_get_named_profile")
    
    print("\nAll tests passed!")

