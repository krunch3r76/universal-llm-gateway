"""
Test cases for ModelId __eq__ and __hash__ methods.
"""

from model_id import ModelId


def test_modelid_eq_string():
    """Test ModelId equality with strings."""
    m1 = ModelId.parse("model-8192")
    assert m1 == "model-8192-hybrid"  # Should be True
    assert m1 == "model-8192"  # Should be True
    assert m1 != "model-8192-cpu"  # Should be False (different normalized)


def test_modelid_eq_modelid():
    """Test ModelId equality with ModelId."""
    m1 = ModelId.parse("model-8192")
    m2 = ModelId.parse("model-8192-hybrid")
    assert m1 == m2  # Should be True


def test_modelid_hash():
    """Test ModelId hashing for dict/set usage."""
    m1 = ModelId.parse("model-8192")
    m2 = ModelId.parse("model-8192-hybrid")
    assert hash(m1) == hash(m2)  # Should be True

    # Can use as dict key
    d = {m1: "value"}
    assert m2 in d  # Should be True


def test_modelid_eq_invalid_string():
    """Test ModelId equality with invalid strings."""
    m1 = ModelId.parse("model-8192")
    assert m1 != ""  # Should be False
    assert m1 != "invalid-model-id"  # Should be False


def test_modelid_eq_other_types():
    """Test ModelId equality with other types returns NotImplemented."""
    m1 = ModelId.parse("model-8192")
    result = m1.__eq__(42)
    assert result is NotImplemented
    result = m1.__eq__(None)
    assert result is NotImplemented


def test_bare_cloud_native():
    """Bare cloud IDs route to native provider — no routing_layer prefix."""
    m = ModelId.parse("anthropic/claude-sonnet-4")
    assert m.routing_layer is None
    assert m.provider == "anthropic"
    assert m.base_id == "anthropic/claude-sonnet-4"
    assert m.api_model_id == "claude-sonnet-4"
    assert m.is_cloud is True
    assert m.original == "anthropic/claude-sonnet-4"


def test_routing_layer_openrouter():
    """openrouter/ prefix preserves provider/model in api_model_id."""
    m = ModelId.parse("openrouter/anthropic/claude-3.5-sonnet")
    assert m.routing_layer == "openrouter"
    assert m.provider == "anthropic"
    assert m.base_id == "anthropic/claude-3.5-sonnet"
    assert m.api_model_id == "anthropic/claude-3.5-sonnet"
    assert m.is_cloud is True


def test_routing_layer_none_for_bare_cloud():
    """Cloud models without routing prefix have routing_layer=None."""
    m = ModelId.parse("anthropic/claude-sonnet-4-20250514")
    assert m.routing_layer is None
    assert m.provider == "anthropic"
    assert m.api_model_id == "claude-sonnet-4-20250514"


def test_routing_layer_none_for_local():
    """Local models have routing_layer=None and api_model_id == base_id."""
    m = ModelId.parse("gemma-2-9b-it-q4-k-m")
    assert m.routing_layer is None
    assert m.provider is None
    assert m.api_model_id == "gemma-2-9b-it-q4-k-m"


def test_routing_layer_identity():
    """Routing layer is not part of model identity (eq/hash)."""
    m_openrouter = ModelId.parse("openrouter/anthropic/claude-sonnet-4")
    m_bare = ModelId.parse("anthropic/claude-sonnet-4")
    assert m_openrouter == m_bare
    assert hash(m_openrouter) == hash(m_bare)


def test_routing_layer_preserved_by_with_context():
    """with_context() preserves routing_layer."""
    m = ModelId.parse("openrouter/anthropic/claude-sonnet-4")
    m2 = m.with_context(8192)
    assert m2.routing_layer == "openrouter"


def test_xai_native_bare():
    """xAI native models are bare provider/model — no routing prefix."""
    m = ModelId.parse("xai/grok-4-fast-reasoning")
    assert m.routing_layer is None
    assert m.provider == "xai"
    assert m.base_id == "xai/grok-4-fast-reasoning"
    assert m.api_model_id == "grok-4-fast-reasoning"
    assert m.is_cloud is True


if __name__ == "__main__":
    test_modelid_eq_string()
    test_modelid_eq_modelid()
    test_modelid_hash()
    test_modelid_eq_invalid_string()
    test_modelid_eq_other_types()
    test_bare_cloud_native()
    test_routing_layer_openrouter()
    test_routing_layer_none_for_bare_cloud()
    test_routing_layer_none_for_local()
    test_routing_layer_identity()
    test_routing_layer_preserved_by_with_context()
    test_xai_native_bare()
    print("All tests passed!")
