"""
Tests for serialization abstraction layer.

These tests verify that all serializers work correctly and handle
edge cases properly.
"""

import pytest

from universal_transport.core.protocol.serializers import (
    DeserializeError,
    JSONSerializer,
    MessagePackSerializer,
    ProtobufSerializer,
    RawBinarySerializer,
    SerializeError,
    get_serializer_by_name,
    list_available_serializers,
)


class TestJSONSerializer:
    """Test JSON serializer."""

    def test_basic_serialization(self):
        """Test basic JSON serialization."""
        serializer = JSONSerializer()

        # Test data
        data = {
            "string": "hello",
            "number": 42,
            "float": 3.14,
            "boolean": True,
            "null": None,
            "array": [1, 2, 3],
            "object": {"nested": "value"},
        }

        # Serialize
        serialized = serializer.serialize(data)
        assert isinstance(serialized, bytes)

        # Deserialize
        deserialized = serializer.deserialize(serialized)
        assert deserialized == data

    def test_unicode_handling(self):
        """Test Unicode string handling."""
        serializer = JSONSerializer()

        data = {"unicode": "Hello 世界 🌍", "emoji": "🚀✨", "special_chars": "åäö"}

        serialized = serializer.serialize(data)
        deserialized = serializer.deserialize(serialized)

        assert deserialized == data

    def test_large_data(self):
        """Test large data serialization."""
        serializer = JSONSerializer()

        # Create large data structure
        data = {"large_array": list(range(10000)), "large_string": "x" * 100000}

        serialized = serializer.serialize(data)
        deserialized = serializer.deserialize(serialized)

        assert deserialized == data

    def test_serialization_options(self):
        """Test JSON serializer options."""
        # Test with ensure_ascii=True
        serializer_ascii = JSONSerializer(ensure_ascii=True)
        data = {"unicode": "hello 世界"}

        serialized_ascii = serializer_ascii.serialize(data)
        # Should not contain raw unicode bytes when ensure_ascii=True
        assert b"\xe4\xb8\x96" not in serialized_ascii  # Raw UTF-8 for 世

        # Test with sort_keys=True
        serializer_sorted = JSONSerializer(sort_keys=True)
        data = {"z": 1, "a": 2, "b": 3}

        serialized_sorted = serializer_sorted.serialize(data)
        json_str = serialized_sorted.decode("utf-8")

        # Should be sorted order
        assert json_str.index('"a"') < json_str.index('"b"') < json_str.index('"z"')

    def test_invalid_data(self):
        """Test handling of non-serializable data."""
        serializer = JSONSerializer()

        # Test with non-serializable object
        class NonSerializable:
            pass

        with pytest.raises(SerializeError):
            serializer.serialize(NonSerializable())

    def test_invalid_bytes(self):
        """Test handling of invalid JSON bytes."""
        serializer = JSONSerializer()

        # Invalid JSON
        with pytest.raises(DeserializeError):
            serializer.deserialize(b"invalid json")

        # Invalid UTF-8
        with pytest.raises(DeserializeError):
            serializer.deserialize(b"\xff\xfe\xfd")


class TestMessagePackSerializer:
    """Test MessagePack serializer."""

    def test_basic_serialization(self):
        """Test basic MessagePack serialization."""
        try:
            serializer = MessagePackSerializer()
        except ImportError:
            pytest.skip("MessagePack not available")

        # Test data
        data = {
            "string": "hello",
            "number": 42,
            "float": 3.14,
            "boolean": True,
            "null": None,
            "array": [1, 2, 3],
            "object": {"nested": "value"},
        }

        # Serialize
        serialized = serializer.serialize(data)
        assert isinstance(serialized, bytes)

        # Deserialize
        deserialized = serializer.deserialize(serialized)
        assert deserialized == data

    def test_binary_data(self):
        """Test binary data handling with MessagePack."""
        try:
            serializer = MessagePackSerializer()
        except ImportError:
            pytest.skip("MessagePack not available")

        data = {"binary": b"\x00\x01\x02\xff\xfe\xfd", "text": "normal text"}

        serialized = serializer.serialize(data)
        deserialized = serializer.deserialize(serialized)

        assert deserialized == data

    def test_size_efficiency(self):
        """Test that MessagePack is more efficient than JSON."""
        try:
            msgpack_serializer = MessagePackSerializer()
        except ImportError:
            pytest.skip("MessagePack not available")

        json_serializer = JSONSerializer()

        # Test with data that should compress well
        data = {"numbers": list(range(1000)), "repeated": ["same_string"] * 100}

        json_size = len(json_serializer.serialize(data))
        msgpack_size = len(msgpack_serializer.serialize(data))

        # MessagePack should be smaller
        assert msgpack_size < json_size

        # Efficiency should be significant (at least 20% smaller)
        efficiency = (json_size - msgpack_size) / json_size
        assert efficiency > 0.2


class TestRawBinarySerializer:
    """Test raw binary serializer."""

    def test_bytes_passthrough(self):
        """Test bytes pass-through."""
        serializer = RawBinarySerializer()

        data = b"raw binary data \x00\x01\x02\xff"

        serialized = serializer.serialize(data)
        assert serialized == data  # Should be identical

        deserialized = serializer.deserialize(serialized)
        assert deserialized == data

    def test_bytearray_handling(self):
        """Test bytearray handling."""
        serializer = RawBinarySerializer()

        data = bytearray(b"bytearray data")

        serialized = serializer.serialize(data)
        assert isinstance(serialized, bytes)

        deserialized = serializer.deserialize(serialized)
        assert deserialized == bytes(data)

    def test_memoryview_handling(self):
        """Test memoryview handling."""
        serializer = RawBinarySerializer()

        original = b"memoryview data"
        data = memoryview(original)

        serialized = serializer.serialize(data)
        assert isinstance(serialized, bytes)
        assert serialized == original

        deserialized = serializer.deserialize(serialized)
        assert deserialized == original

    def test_invalid_data_types(self):
        """Test handling of non-bytes data."""
        serializer = RawBinarySerializer()

        # Should fail with non-bytes data
        with pytest.raises(SerializeError):
            serializer.serialize("string")

        with pytest.raises(SerializeError):
            serializer.serialize(42)

        with pytest.raises(SerializeError):
            serializer.serialize({"dict": "data"})


class TestProtobufSerializer:
    """Test Protobuf serializer."""

    def test_without_message_class(self):
        """Test Protobuf serializer without message class."""
        try:
            serializer = ProtobufSerializer()
        except ImportError:
            pytest.skip("Protobuf not available")

        # Should fail without message class
        with pytest.raises(SerializeError):
            serializer.serialize({"data": "test"})

        with pytest.raises(DeserializeError):
            serializer.deserialize(b"test data")


class TestSerializerRegistry:
    """Test serializer registry functions."""

    def test_get_serializer_by_name(self):
        """Test getting serializers by name."""
        # JSON should always be available
        json_serializer = get_serializer_by_name("json")
        assert isinstance(json_serializer, JSONSerializer)

        # Test case insensitivity
        json_serializer2 = get_serializer_by_name("JSON")
        assert isinstance(json_serializer2, JSONSerializer)

        # Test MIME type
        json_serializer3 = get_serializer_by_name("application/json")
        assert isinstance(json_serializer3, JSONSerializer)

        # Raw binary should always be available
        raw_serializer = get_serializer_by_name("raw")
        assert isinstance(raw_serializer, RawBinarySerializer)

        # Test unknown serializer
        with pytest.raises(ValueError):
            get_serializer_by_name("unknown")

    def test_list_available_serializers(self):
        """Test listing available serializers."""
        serializers = list_available_serializers()

        # JSON and raw should always be available
        assert "json" in serializers
        assert "raw" in serializers

        # Check descriptions
        assert "JSON" in serializers["json"]
        assert "binary" in serializers["raw"]


class TestSerializerInterface:
    """Test serializer interface compliance."""

    def test_serializer_interface(self):
        """Test that all serializers implement the interface correctly."""
        serializers = [JSONSerializer(), RawBinarySerializer()]

        # Add MessagePack if available
        try:
            serializers.append(MessagePackSerializer())
        except ImportError:
            pass

        for serializer in serializers:
            # Check interface compliance
            assert hasattr(serializer, "serialize")
            assert hasattr(serializer, "deserialize")
            assert hasattr(serializer, "name")
            assert hasattr(serializer, "content_type")

            # Check string representations
            assert isinstance(str(serializer), str)
            assert isinstance(repr(serializer), str)

            # Check metadata
            assert isinstance(serializer.name, str)
            assert isinstance(serializer.content_type, str)

    def test_round_trip_consistency(self):
        """Test round-trip consistency for all serializers."""
        test_data = {
            "string": "test",
            "number": 42,
            "array": [1, 2, 3],
            "nested": {"key": "value"},
        }

        serializers = [JSONSerializer()]

        # Add MessagePack if available
        try:
            serializers.append(MessagePackSerializer())
        except ImportError:
            pass

        for serializer in serializers:
            serialized = serializer.serialize(test_data)
            deserialized = serializer.deserialize(serialized)
            assert deserialized == test_data

        # Test raw binary with bytes data
        raw_serializer = RawBinarySerializer()
        raw_data = b"binary test data"
        serialized = raw_serializer.serialize(raw_data)
        deserialized = raw_serializer.deserialize(serialized)
        assert deserialized == raw_data
