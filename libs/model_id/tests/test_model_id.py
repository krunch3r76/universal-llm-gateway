"""
Unit tests for ModelId routing_key semantics.

Verifies that routing_key now includes context length to properly
distinguish different context variants.
"""

from model_id import ModelId


def test_routing_key_includes_context():
    """Different context lengths produce different routing keys."""
    model_16k = ModelId.parse("hermes3-llama-70b-16384-hybrid")
    model_24k = ModelId.parse("hermes3-llama-70b-24000-hybrid")

    assert model_16k.routing_key != model_24k.routing_key
    assert model_16k.routing_key == "hermes3-llama-70b-16384"
    assert model_24k.routing_key == "hermes3-llama-70b-24000"


def test_routing_key_strips_hybrid():
    """Hybrid and non-hybrid share routing key."""
    hybrid = ModelId.parse("model-8192-hybrid")
    non_hybrid = ModelId.parse("model-8192")

    assert hybrid.routing_key == non_hybrid.routing_key
    assert hybrid.routing_key == "model-8192"


def test_routing_key_equals_normalized():
    """Routing key equals normalized for all cases."""
    cases = [
        "model-8192",
        "model-8192-hybrid",
        "model-8192-cpu",
        "model",
        "model-cpu",
        "anthropic/claude-sonnet-4-6-mcp",
    ]
    for case in cases:
        model = ModelId.parse(case)
        assert model.routing_key == model.normalized


def test_routing_key_no_context():
    """Models without context length have routing_key = base_id."""
    model = ModelId.parse("hermes3-llama-70b")
    assert model.routing_key == "hermes3-llama-70b"
    assert model.routing_key == model.base_id


def test_routing_key_cpu_suffix():
    """CPU suffix is preserved in routing_key."""
    model = ModelId.parse("model-8192-cpu")
    assert model.routing_key == "model-8192-cpu"

    model_no_context = ModelId.parse("model-cpu")
    assert model_no_context.routing_key == "model-cpu"


def test_routing_key_mcp_suffix():
    """-mcp suffix is identity-bearing in routing_key."""
    m = ModelId.parse("anthropic/claude-sonnet-4-6-mcp")
    assert m.routing_key == "anthropic/claude-sonnet-4-6-mcp"
    assert m.routing_key != ModelId.parse("anthropic/claude-sonnet-4-6").routing_key


def test_synthetic_id_hybrid_mcp():
    """synthetic_id preserves -hybrid then -mcp order."""
    m = ModelId.parse("model-8192-hybrid-mcp")
    assert m.synthetic_id == "model-8192-hybrid-mcp"


def test_cursor_provider_backend_type_is_cursor_sdk():
    model = ModelId.parse("cursor/claude-sonnet-5")
    assert model.backend_type == "cursor_sdk"
    assert model.is_cloud is False
    assert model.provider == "cursor"


def test_anthropic_provider_backend_type_is_cloud_api():
    model = ModelId.parse("anthropic/claude-sonnet-5")
    assert model.backend_type == "cloud_api"
    assert model.is_cloud is True
    assert model.provider == "anthropic"


def test_cdp_provider_backend_type_is_cdp():
    model = ModelId.parse("cdp/opus-4.8")
    assert model.backend_type == "cdp"
    assert model.is_cloud is False
    assert model.provider == "cdp"
    assert model.base_id == "cdp/opus-4.8"


def test_cdp_picker_passthrough_fable():
    model = ModelId.parse("cdp/fable-5")
    assert model.backend_type == "cdp"
    assert model.provider == "cdp"
    assert model.base_id == "cdp/fable-5"


if __name__ == "__main__":
    # Run all tests
    test_routing_key_includes_context()
    test_routing_key_strips_hybrid()
    test_routing_key_equals_normalized()
    test_routing_key_no_context()
    test_routing_key_cpu_suffix()
    test_routing_key_mcp_suffix()
    test_synthetic_id_hybrid_mcp()
    print("✅ All tests passed")
