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


if __name__ == "__main__":
    test_modelid_eq_string()
    test_modelid_eq_modelid()
    test_modelid_hash()
    test_modelid_eq_invalid_string()
    test_modelid_eq_other_types()
    print("All tests passed!")
