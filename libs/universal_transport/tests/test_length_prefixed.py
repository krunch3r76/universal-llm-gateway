"""
Tests for length-prefixed protocol implementation.

These tests verify the core framing protocol that eliminates
asyncio readline buffer limit issues.
"""

import struct

import pytest

from universal_transport.core.protocol.base import DecodeError, EncodeError
from universal_transport.core.protocol.length_prefixed import (
    DecodingState,
    LengthPrefixedProtocol,
    create_json_protocol,
    create_messagepack_protocol,
    create_raw_protocol,
)
from universal_transport.core.protocol.serializers import (
    JSONSerializer,
    RawBinarySerializer,
)


class TestLengthPrefixedProtocol:
    """Test length-prefixed protocol implementation."""

    def test_basic_encode_decode(self):
        """Test basic message encoding and decoding."""
        protocol = LengthPrefixedProtocol(JSONSerializer())

        message = {"type": "test", "data": "hello world"}

        # Encode
        encoded = protocol.encode(message)
        assert isinstance(encoded, bytes)
        assert len(encoded) > 4  # Should have length prefix + payload

        # Decode
        messages = list(protocol.decode_stream(encoded))
        assert len(messages) == 1
        assert messages[0] == message

    def test_length_prefix_format(self):
        """Test that length prefix is correct 4-byte big-endian format."""
        protocol = LengthPrefixedProtocol(JSONSerializer())

        message = {"test": "data"}
        encoded = protocol.encode(message)

        # Extract length prefix
        length_prefix = encoded[:4]
        payload = encoded[4:]

        # Decode length prefix
        payload_length = struct.unpack("!I", length_prefix)[0]

        # Verify
        assert payload_length == len(payload)
        assert len(length_prefix) == 4

    def test_multiple_messages(self):
        """Test encoding and decoding multiple messages."""
        protocol = LengthPrefixedProtocol(JSONSerializer())

        messages = [
            {"id": 1, "data": "first"},
            {"id": 2, "data": "second"},
            {"id": 3, "data": "third"},
        ]

        # Encode all messages
        encoded_data = b""
        for msg in messages:
            encoded_data += protocol.encode(msg)

        # Decode all messages
        decoded_messages = list(protocol.decode_stream(encoded_data))

        assert len(decoded_messages) == len(messages)
        assert decoded_messages == messages

    def test_partial_message_handling(self):
        """Test handling of partial messages (streaming scenario)."""
        protocol = LengthPrefixedProtocol(JSONSerializer())

        message = {"data": "test message"}
        encoded = protocol.encode(message)

        # Split encoded data into chunks
        chunk1 = encoded[:2]  # Partial length prefix
        chunk2 = encoded[2:6]  # Rest of length prefix + partial payload
        chunk3 = encoded[6:]  # Rest of payload

        # Decode chunks incrementally
        messages1 = list(protocol.decode_stream(chunk1))
        assert len(messages1) == 0  # No complete messages yet

        messages2 = list(protocol.decode_stream(chunk2))
        assert len(messages2) == 0  # Still no complete messages

        messages3 = list(protocol.decode_stream(chunk3))
        assert len(messages3) == 1  # Now we have complete message
        assert messages3[0] == message

    def test_state_machine_transitions(self):
        """Test decoder state machine transitions."""
        protocol = LengthPrefixedProtocol(JSONSerializer())

        # Initial state
        assert protocol._state == DecodingState.READING_LENGTH
        assert protocol._expected_length == 0

        message = {"test": "state machine"}
        encoded = protocol.encode(message)

        # Feed partial data (just length prefix)
        length_prefix = encoded[:4]
        messages = list(protocol.decode_stream(length_prefix))

        # Should transition to reading payload
        assert protocol._state == DecodingState.READING_PAYLOAD
        assert protocol._expected_length > 0
        assert len(messages) == 0

        # Feed payload
        payload = encoded[4:]
        messages = list(protocol.decode_stream(payload))

        # Should transition back to reading length
        assert protocol._state == DecodingState.READING_LENGTH
        assert protocol._expected_length == 0
        assert len(messages) == 1
        assert messages[0] == message

    def test_large_message_handling(self):
        """Test handling of large messages."""
        protocol = LengthPrefixedProtocol(
            JSONSerializer(),
            max_message_size=10 * 1024 * 1024,  # 10MB limit
        )

        # Create large message
        large_data = "x" * (1024 * 1024)  # 1MB string
        large_message = {"large_data": large_data}

        # Should encode successfully
        encoded = protocol.encode(large_message)
        assert len(encoded) > 1024 * 1024  # Should be > 1MB

        # Should decode successfully
        messages = list(protocol.decode_stream(encoded))
        assert len(messages) == 1
        assert messages[0] == large_message

    def test_size_limit_enforcement(self):
        """Test message size limit enforcement."""
        protocol = LengthPrefixedProtocol(
            JSONSerializer(),
            max_message_size=1024,  # 1KB limit
        )

        # Create message larger than limit
        large_data = "x" * 2000  # 2KB string
        large_message = {"data": large_data}

        # Encoding should fail
        with pytest.raises(EncodeError):
            protocol.encode(large_message)

        # Test decoding size limit
        # Create a fake frame with oversized length prefix
        fake_length = 2000  # Larger than 1KB limit
        fake_frame = struct.pack("!I", fake_length) + b"x" * 100

        # Decoding should fail
        with pytest.raises(DecodeError):
            list(protocol.decode_stream(fake_frame))

    def test_zero_length_message(self):
        """Test handling of zero-length messages."""
        protocol = LengthPrefixedProtocol(RawBinarySerializer())

        empty_data = b""

        # Should encode successfully
        encoded = protocol.encode(empty_data)
        assert len(encoded) == 4  # Just the length prefix

        # Verify length prefix is zero
        length_prefix = encoded[:4]
        payload_length = struct.unpack("!I", length_prefix)[0]
        assert payload_length == 0

        # Should decode successfully
        messages = list(protocol.decode_stream(encoded))
        assert len(messages) == 1
        assert messages[0] == empty_data

    def test_corrupted_data_handling(self):
        """Test handling of corrupted protocol data."""
        protocol = LengthPrefixedProtocol(JSONSerializer())

        # Test invalid length prefix (too short)
        with pytest.raises(DecodeError):
            list(protocol.decode_stream(b"\x00\x00"))  # Only 2 bytes

        # Test length-payload mismatch
        # Say length is 10 but only provide 5 bytes of payload
        fake_frame = struct.pack("!I", 10) + b"short"
        messages = list(protocol.decode_stream(fake_frame))
        assert len(messages) == 0  # Should not yield incomplete message

        # Buffer should be waiting for more data
        assert len(protocol._decode_buffer) > 0

    def test_decoder_reset(self):
        """Test decoder state reset functionality."""
        protocol = LengthPrefixedProtocol(JSONSerializer())

        # Put decoder in partial state
        partial_data = b"\x00\x00\x00\x10abc"  # Partial payload
        list(protocol.decode_stream(partial_data))

        # Should be in payload reading state with buffer
        assert protocol._state == DecodingState.READING_PAYLOAD
        assert len(protocol._decode_buffer) > 0

        # Reset decoder
        protocol.reset_decoder()

        # Should be back to initial state
        assert protocol._state == DecodingState.READING_LENGTH
        assert len(protocol._decode_buffer) == 0
        assert protocol._expected_length == 0

    def test_serializer_swapping(self):
        """Test changing serializers."""
        protocol = LengthPrefixedProtocol(JSONSerializer())

        # Test with JSON
        json_message = {"type": "json", "data": "test"}
        encoded_json = protocol.encode(json_message)
        decoded = list(protocol.decode_stream(encoded_json))
        assert decoded[0] == json_message

        # Switch to raw binary
        protocol.set_serializer(RawBinarySerializer())

        # Test with binary data
        binary_message = b"binary data"
        encoded_binary = protocol.encode(binary_message)
        decoded = list(protocol.decode_stream(encoded_binary))
        assert decoded[0] == binary_message

    def test_message_validation(self):
        """Test message validation before encoding."""
        protocol = LengthPrefixedProtocol(JSONSerializer())

        # Valid message
        valid_message = {"test": "valid"}
        assert protocol.validate_message(valid_message)

        # Invalid message (for JSON serializer)
        class NonSerializable:
            pass

        invalid_message = NonSerializable()
        assert not protocol.validate_message(invalid_message)

    def test_size_estimation(self):
        """Test encoded size estimation."""
        protocol = LengthPrefixedProtocol(JSONSerializer())

        message = {"data": "test message"}

        # Estimate size
        estimated_size = protocol.estimate_encoded_size(message)

        # Actual encoding
        actual_encoded = protocol.encode(message)
        actual_size = len(actual_encoded)

        # Should match exactly
        assert estimated_size == actual_size

        # Should include 4-byte length prefix
        assert estimated_size > len(
            message.__str__()
        )  # Larger than string representation

    def test_protocol_info(self):
        """Test protocol information methods."""
        protocol = LengthPrefixedProtocol(JSONSerializer())

        # Test string representations
        protocol_str = str(protocol)
        assert "LengthPrefixedProtocol" in protocol_str
        assert "JSON" in protocol_str

        protocol_repr = repr(protocol)
        assert "LengthPrefixedProtocol" in protocol_repr

        # Test decoder state info
        state_info = protocol.get_decoder_state()
        assert "state" in state_info
        assert "buffer_size" in state_info
        assert "max_message_size" in state_info
        assert "serializer" in state_info


class TestProtocolFactories:
    """Test protocol factory functions."""

    def test_create_json_protocol(self):
        """Test JSON protocol factory."""
        protocol = create_json_protocol()

        assert isinstance(protocol, LengthPrefixedProtocol)
        assert isinstance(protocol.serializer, JSONSerializer)
        assert (
            protocol.max_message_size == LengthPrefixedProtocol.DEFAULT_MAX_MESSAGE_SIZE
        )

        # Test with custom size
        custom_protocol = create_json_protocol(max_message_size=1024)
        assert custom_protocol.max_message_size == 1024

    def test_create_messagepack_protocol(self):
        """Test MessagePack protocol factory."""
        try:
            protocol = create_messagepack_protocol()
            assert isinstance(protocol, LengthPrefixedProtocol)
            # Can't easily test MessagePackSerializer type without importing msgpack
        except ImportError:
            pytest.skip("MessagePack not available")

    def test_create_raw_protocol(self):
        """Test raw binary protocol factory."""
        protocol = create_raw_protocol()

        assert isinstance(protocol, LengthPrefixedProtocol)
        assert isinstance(protocol.serializer, RawBinarySerializer)

    def test_factory_consistency(self):
        """Test that factory functions create working protocols."""
        factories = [create_json_protocol, create_raw_protocol]

        # Add MessagePack if available
        try:
            factories.append(create_messagepack_protocol)
        except ImportError:
            pass

        for factory in factories:
            protocol = factory()

            # Should be able to encode/decode
            if isinstance(protocol.serializer, RawBinarySerializer):
                test_data = b"binary test data"
            else:
                test_data = {"test": "factory data"}

            encoded = protocol.encode(test_data)
            decoded = list(protocol.decode_stream(encoded))

            assert len(decoded) == 1
            assert decoded[0] == test_data


class TestProtocolConstants:
    """Test protocol constants and limits."""

    def test_constants(self):
        """Test protocol constants."""
        assert LengthPrefixedProtocol.LENGTH_PREFIX_SIZE == 4
        assert (
            LengthPrefixedProtocol.DEFAULT_MAX_MESSAGE_SIZE == 100 * 1024 * 1024
        )  # 100MB
        assert LengthPrefixedProtocol.MAX_THEORETICAL_SIZE == 2**32 - 1  # ~4GB

    def test_max_size_validation(self):
        """Test maximum size validation during initialization."""
        # Should work with valid sizes
        protocol = LengthPrefixedProtocol(JSONSerializer(), max_message_size=1024)
        assert protocol.max_message_size == 1024

        # Should fail with invalid sizes
        with pytest.raises(ValueError):
            LengthPrefixedProtocol(JSONSerializer(), max_message_size=0)

        with pytest.raises(ValueError):
            LengthPrefixedProtocol(JSONSerializer(), max_message_size=-1)

        with pytest.raises(ValueError):
            LengthPrefixedProtocol(
                JSONSerializer(), max_message_size=2**33
            )  # Too large
