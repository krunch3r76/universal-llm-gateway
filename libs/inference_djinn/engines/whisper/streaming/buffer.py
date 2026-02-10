"""Efficient audio buffer using deque of chunks for O(1) prepend/append operations."""

from collections import deque

import numpy as np


class EfficientAudioBuffer:
    """
    Efficient audio buffer using deque of chunks for O(1) prepend/append operations.

    Supports efficient:
    - Appending new audio data
    - Prepending unprocessed audio
    - Random access by byte offset
    - Removing processed audio from the front
    """

    def __init__(self, chunk_size: int = 8192):
        """Initialize buffer with configurable chunk size (8KB default)."""
        self.chunks: deque = deque()
        self.chunk_size = chunk_size
        self._total_bytes = 0

    def append(self, data: bytes | bytearray | np.ndarray) -> None:
        """Append audio data to the end of the buffer."""
        if isinstance(data, np.ndarray):
            if data.dtype == np.float32:
                data = (data * 32767).astype(np.int16)
            data = data.tobytes()
        elif isinstance(data, bytearray):
            data = bytes(data)

        self.chunks.append(data)
        self._total_bytes += len(data)

    def prepend(self, data: bytes | bytearray) -> None:
        """Prepend audio data to the beginning of the buffer (O(1) operation)."""
        if isinstance(data, bytearray):
            data = bytes(data)

        self.chunks.appendleft(data)
        self._total_bytes += len(data)

    def __len__(self) -> int:
        """Return total number of bytes in buffer."""
        return self._total_bytes

    def __getitem__(self, key: int | slice) -> bytes:
        """Get bytes from buffer by index or slice."""
        if isinstance(key, slice):
            start, stop, step = key.indices(self._total_bytes)
            if step != 1:
                raise ValueError("Step not supported")
            return self._get_range(start, stop)
        else:
            if key < 0:
                key += self._total_bytes
            if key >= self._total_bytes or key < 0:
                raise IndexError("Buffer index out of range")
            return self._get_range(key, key + 1)

    def _get_range(self, start: int, stop: int) -> bytes:
        """Get bytes from start to stop position."""
        if start >= stop:
            return b""

        result = bytearray()
        current_pos = 0

        for chunk in self.chunks:
            chunk_end = current_pos + len(chunk)

            if current_pos >= stop:
                break

            if chunk_end > start:
                chunk_start = max(0, start - current_pos)
                chunk_stop = min(len(chunk), stop - current_pos)
                result.extend(chunk[chunk_start:chunk_stop])

            current_pos = chunk_end

        return bytes(result)

    def remove_front(self, num_bytes: int) -> bytes:
        """Remove and return specified number of bytes from the front."""
        if num_bytes <= 0:
            return b""

        removed_data = bytearray()
        bytes_to_remove = min(num_bytes, self._total_bytes)

        while bytes_to_remove > 0 and self.chunks:
            chunk = self.chunks[0]

            if len(chunk) <= bytes_to_remove:
                removed_chunk = self.chunks.popleft()
                removed_data.extend(removed_chunk)
                bytes_to_remove -= len(removed_chunk)
                self._total_bytes -= len(removed_chunk)
            else:
                removed_data.extend(chunk[:bytes_to_remove])
                remaining_chunk = chunk[bytes_to_remove:]
                self.chunks[0] = remaining_chunk
                self._total_bytes -= bytes_to_remove
                bytes_to_remove = 0

        return bytes(removed_data)

    def clear(self) -> None:
        """Clear all data from the buffer."""
        self.chunks.clear()
        self._total_bytes = 0

    def to_bytes(self) -> bytes:
        """Convert entire buffer to a single bytes object."""
        return b"".join(self.chunks)

    def to_numpy_int16(self) -> np.ndarray:
        """Convert buffer to numpy int16 array."""
        return np.frombuffer(self.to_bytes(), dtype=np.int16)

    def to_numpy_float32(self) -> np.ndarray:
        """Convert buffer to numpy float32 array normalized to [-1.0, 1.0]."""
        return self.to_numpy_int16().astype(np.float32) / 32768.0

