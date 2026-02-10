# universal_protocol

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Overview

**universal_protocol** is a high-performance, transport-agnostic protocol library for the Universal LLM Ecosystem. It provides a unified interface for JSON-RPC 2.0 communication, WebSocket streaming, and server-sent events (SSE) over Unix domain sockets.

## Key Features

- **JSON-RPC 2.0 Support**: Full JSON-RPC 2.0 client and server implementation with error handling
- **WebSocket Streaming**: Efficient WebSocket-based streaming over Unix sockets with SSE formatting
- **Unix Domain Socket Transport**: Low-latency IPC using Unix domain sockets
- **State Channel Protocol**: Resilient state synchronization with automatic reconnection
- **Observability**: Built-in metrics, logging, and debugging capabilities
- **Resource Management**: Stream lifecycle management with backpressure handling
- **ASGI Application**: Starlette-based ASGI app with RPC and streaming endpoints
- **Type-Safe**: Full type hints and dataclass-based message protocols

## Scope Invariant (CRITICAL)

**Invariant:** `universal_protocol ∩ service_specific_code = ∅`

`universal_protocol` is **ECOSYSTEM-AGNOSTIC**. It provides:
- Generic WebSocket primitives (bounded queue, connection handling)
- Generic RPC patterns (JSON-RPC 2.0)
- Generic SSE handling
- Wire format utilities
- State channel protocols

**FORBIDDEN:** Service-specific protocols (federation, inference, etc.)
These belong in their respective service directories.

**Symptom if violated:** 
- Circular dependencies between libs and services
- Tight coupling preventing independent testing
- Service-specific logic leaking into shared infrastructure

**Example of correct boundary:**
```python
# ✅ CORRECT: Generic primitive in libs/
from universal_protocol.ws.bounded_queue import BoundedQueue

# ✅ CORRECT: Service-specific protocol in service
from systems.federation.protocol import FederationWebSocketClient

# ❌ VIOLATION: Federation code was in libs/ (now fixed)
# from universal_protocol.federation import FederationWebSocketClient
```

## Installation

### Prerequisites

- Python 3.12+
- Git

### Virtual Environment

**All Universal LLM Ecosystem components use a shared virtual environment:**

```bash
# Create the shared virtual environment (if not already created)
python3.12 -m venv $HOME/.venvs/universal

# Activate
source $HOME/.venvs/universal/bin/activate

# Verify Python version
python --version  # Should be Python 3.12.x or higher
```

**Important**: The shared virtual environment `$HOME/.venvs/universal` is used across all ecosystem components (universal-llm-gateway, universal-stargate, universal_logging, universal-event-bus, universal_protocol, etc.). This ensures consistent Python versions and allows ecosystem components to import each other.

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Ecosystem Components

This component depends on other Universal LLM Ecosystem packages. Ensure they are accessible in the Python path (they should be in the shared venv or PYTHONPATH):

- **universal_logging**: Structured logging for the ecosystem
- **universal-event-bus**: Event-driven messaging (optional)

## Architecture

### Core Components

1. **RPC Module** (`rpc/`):
   - `client.py`: JSON-RPC 2.0 client (sync and async)
   - `handlers.py`: RPC method handlers
   - `types.py`: Type definitions for RPC messages

2. **WebSocket Module** (`ws/`):
   - `client.py`: WebSocket stream client
   - `handlers.py`: WebSocket stream handlers
   - `stream_queue.py`: Unbounded stream queue
   - `bounded_queue.py`: Bounded queue with backpressure
   - `lifecycle.py`: Stream lifecycle management

3. **SSE Module** (`sse/`):
   - `core.py`: SSE formatting and parsing utilities

4. **State Channel Module** (`state_channel/`):
   - `channel.py`: Basic state synchronization channel
   - `resilient_channel.py`: Reconnection-aware state channel
   - `protocol.py`: State protocol message types

5. **Server Module** (`server/`):
   - `asgi_app.py`: Starlette ASGI application
   - `startup.py`: Server startup and configuration
   - `uds_security.py`: Unix socket security and permissions

6. **Observability** (`observability.py`):
   - Metrics collection (RPC, streams, backpressure)
   - Prometheus-compatible metrics export
   - Debug statistics

## Usage

### Starting a Server

```python
from universal_protocol.server import serve

# Start server on Unix socket
serve(
    socket_path="/tmp/universal-protocol/worker.sock",
    loop="uvloop",  # or "asyncio"
)
```

### RPC Client

```python
from universal_protocol.rpc import RPCClient

# Create client
async with RPCClient("/tmp/universal-protocol/worker.sock") as client:
    # Call RPC methods
    health = await client.health()
    print(health)  # {"status": "ready", "models": [...]}
    
    # Start inference
    result = await client.call("start_inference", {
        "prompt": "Once upon a time",
        "max_tokens": 100
    })
    stream_id = result["stream_id"]
```

### Stream Client

```python
from universal_protocol.ws import StreamClient

# Connect to stream
async with StreamClient(socket_path, stream_id) as stream:
    async for message in stream.iter_messages():
        if message["t"] == "token":
            print(message["txt"], end="", flush=True)
        elif message["t"] == "done":
            print(f"\nUsage: {message['usage']}")
```

### State Channel

```python
from universal_protocol.state_channel import ResilientStateChannel

# Create state channel with automatic reconnection
channel = ResilientStateChannel(
    socket_path="/tmp/universal-protocol/state.sock",
    on_state_update=lambda state: print(f"State: {state}"),
    reconnect_delay=1.0,
)

await channel.connect()
await channel.send_update({"key": "value"})
```

## Configuration

Configuration is loaded from `config.yaml`:

```yaml
protocol:
  socket_dir: /tmp/universal-protocol

server:
  loop: uvloop  # or asyncio
  workers: 1
```

## Metrics

Metrics are available at `/metrics` endpoint:

```bash
# JSON format (default)
curl http://unix:/tmp/universal-protocol/worker.sock:/metrics

# Prometheus format
curl http://unix:/tmp/universal-protocol/worker.sock:/metrics?format=prometheus
```

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for development guidelines, IDE setup, and coding standards.

## Testing

```bash
# Run tests with pytest
pytest

# With coverage
pytest --cov=universal_protocol --cov-report=html
```

## Author

**krunch3r76** ([@krunch3r76](https://github.com/krunch3r76))

- GitHub: [@krunch3r76](https://github.com/krunch3r76)
- Email: biz@u26a4.com

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
