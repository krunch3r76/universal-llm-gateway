# Universal Protocol Phase 1 - Quick Reference

## Installation

Phase 1 modules are in `libs/universal_protocol/`. The `sitecustomize.py` in the project root automatically adds `libs/` to PYTHONPATH.

```bash
# Activate venv
source ~/.venvs/universal/bin/activate
cd /mnt/torus/projects/universal-llm-gateway

# Test imports
python3 -c "from universal_protocol import *; print('✓ Ready')"
```

## SSE Protocol (Server-Sent Events)

### Format a message for streaming

```python
from universal_protocol import format_sse

# Any dict → SSE message
msg = format_sse({"t": "token", "i": 0, "txt": "hello"})
# Returns: 'data: {"t":"token","i":0,"txt":"hello"}\n\n'

# Send over WebSocket:
await websocket.send_text(msg)
```

### Parse received SSE messages

```python
from universal_protocol import parse_sse

# Receive from WebSocket:
raw_msg = 'data: {"t":"token","i":0,"txt":"hello"}\n\n'
data = parse_sse(raw_msg)
# Returns: {'t': 'token', 'i': 0, 'txt': 'hello'}

# Also handles raw JSON:
data = parse_sse('{"t":"token","i":0}')
# Returns: {'t': 'token', 'i': 0}
```

## Error Handling

### Create error responses

```python
from universal_protocol import error_envelope

# Quick envelope creation
err = error_envelope(
    code="OOM",
    message="CUDA out of memory",
    source="engine",
    data={"available_mb": 512}
)
# Returns:
# {
#   "code": "OOM",
#   "message": "CUDA out of memory",
#   "source": "engine",
#   "data": {"available_mb": 512}
# }
```

### Raise typed exceptions

```python
from universal_protocol import RPCError, StreamError, EngineError

# RPC layer error
raise RPCError(
    "INVALID_REQUEST",
    "Missing 'jsonrpc' field",
    {"field": "jsonrpc"}
)

# Stream layer error
raise StreamError(
    "QUEUE_TIMEOUT",
    "Queue full after 500ms",
    {"timeout_ms": 500}
)

# Engine layer error
raise EngineError(
    "MODEL_NOT_FOUND",
    "Model 'llama-3.2' not loaded"
)

# Get error dict
try:
    raise StreamError("CLOSED", "Socket closed")
except StreamError as e:
    error_dict = e.to_dict()
    # {
    #   "code": "CLOSED",
    #   "message": "Socket closed",
    #   "source": "stream"
    # }
```

## ID Generation

### Generate stream IDs for WebSocket connections

```python
from universal_protocol import generate_stream_id

stream_id = generate_stream_id()
# Returns: "stream-f70dc702-f002-4b28-9e1f-413f99472884"

# Custom prefix
batch_id = generate_stream_id(prefix="batch")
# Returns: "batch-550e8400-e29b-41d4-a716-446655440000"
```

### Generate request IDs for RPC correlation

```python
from universal_protocol import generate_request_id

request_id = generate_request_id()
# Returns: "req-bb2f12c3-631e-4377-9b20-0a84d97789e6"

# Custom prefix
task_id = generate_request_id(prefix="task")
# Returns: "task-a1b2c3d4-e5f6-47g8-h9i0-j1k2l3m4n5o6"
```

### Generic ID generation

```python
from universal_protocol import generate_id

session_id = generate_id("session")
# Returns: "session-550e8400-e29b-41d4-a716-446655440000"
```

## Complete Example

### SSE Token Streaming

```python
from universal_protocol import format_sse, parse_sse, error_envelope, generate_stream_id

# Start a stream
stream_id = generate_stream_id()
print(f"Stream {stream_id} started")

# Send tokens
tokens = ["Hello", " ", "world", "!"]
for i, token_text in enumerate(tokens):
    msg = format_sse({
        "t": "token",
        "i": i,
        "txt": token_text
    })
    # Send over WebSocket...

# End stream
done_msg = format_sse({
    "t": "done",
    "usage": {
        "input_tokens": 5,
        "output_tokens": 4
    }
})
# Send over WebSocket...

# Handle error
error_msg = format_sse({
    "t": "err",
    "code": "TIMEOUT",
    "message": "Inference timeout after 30s",
    "source": "engine"
})
# Send over WebSocket...
```

### JSON-RPC Error Response

```python
from universal_protocol import RPCError, error_envelope
import json

try:
    # Validate RPC request
    if "jsonrpc" not in request:
        raise RPCError("INVALID_REQUEST", "Missing 'jsonrpc' field")
    
    # Process...
except RPCError as e:
    # Return JSON-RPC error response
    response = {
        "jsonrpc": "2.0",
        "error": e.to_dict(),
        "id": request.get("id")
    }
    # Send response...
```

## Module Reference

| Function | Purpose | Returns |
|----------|---------|---------|
| `format_sse(data)` | Format dict as SSE message | `str` |
| `parse_sse(msg)` | Parse SSE/JSON message | `dict` |
| `generate_stream_id(prefix)` | Create stream ID | `str` |
| `generate_request_id(prefix)` | Create request ID | `str` |
| `generate_id(prefix)` | Create any ID | `str` |
| `error_envelope(...)` | Create error response | `dict` |

| Exception Class | Use Case | Source Field |
|-----------------|----------|--------------|
| `RPCError` | JSON-RPC validation | `"rpc"` |
| `StreamError` | WebSocket/streaming | `"stream"` |
| `EngineError` | GPU/inference | `"engine"` |

## Key Design Points

- **SSE Format**: Exact `"data: {json}\n\n"` per RFC 9110
- **Error Source**: Helps identify which layer failed (rpc/stream/engine)
- **ID Format**: `"{prefix}-{uuid4}"` for readability and uniqueness
- **No Dependencies**: Uses only Python stdlib (no external packages)
- **Full Type Hints**: All functions annotated for IDE support

## Testing

All modules are tested and working. To verify:

```python
from universal_protocol import (
    format_sse, parse_sse,
    RPCError, StreamError, EngineError, error_envelope,
    generate_stream_id, generate_request_id
)

# Round-trip SSE
msg = format_sse({"t": "token", "i": 0, "txt": "test"})
assert parse_sse(msg) == {"t": "token", "i": 0, "txt": "test"}

# Error envelope
err = RPCError("TEST", "Test error")
assert err.to_dict()["source"] == "rpc"

# ID generation
sid = generate_stream_id()
assert sid.startswith("stream-")

print("✅ All Phase 1 modules verified")
```

---

**Phase 1 Complete** | **Ready for Phase 2** | **Last Updated: November 4, 2025**
