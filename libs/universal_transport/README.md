# Universal Transport - Ecosystem Foundation Layer

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Modern async transport layer with **length-prefixed framing** that eliminates asyncio readline buffer limits and handles multi-MB messages efficiently.

## 🚀 v1.0.0 - Clean Architecture

**BREAKING CHANGES:** Legacy sync code removed, async-only architecture

**Benefits:**
- **🧹 Clean Codebase**: Removed all deprecated sync/JSONL implementations  
- **🚀 Async-Only**: Streamlined API focused on modern async patterns
- **💪 Battle-Tested**: Length-prefixed protocol proven to handle 100MB+ messages
- **🔬 Simple**: One transport layer, one framing protocol, pluggable serialization
- **🚫 No Legacy**: No backward compatibility aliases - clean, explicit API

## Key Features

- **🔥 Length-Prefixed Protocol**: Eliminates readline buffer limits entirely (handles 100MB+ messages)
- **⚡ Async-Only Design**: Uses `asyncio.readexactly()` (no readline issues)
- **🔧 Pluggable Serialization**: JSON, MessagePack, Protobuf, Raw Binary
- **🚀 Multi-MB Payload Support**: No buffer scanning, no delimiter conflicts
- **🔄 process_ipc Migration**: Compatibility helpers for easy migration
- **🛠️ Debug Tools**: Protocol inspection utilities
- **🌐 Async Transports**: Unix sockets (primary), TCP sockets
- **📦 High-Level API**: Simple client/server patterns

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

**Important**: The shared virtual environment `$HOME/.venvs/universal` is used across all ecosystem components (universal-llm-gateway, universal-stargate, universal_logging, universal-event-bus, universal_transport, etc.). This ensures consistent Python versions and allows ecosystem components to import each other.

### Install Dependencies

```bash
# Activate the shared virtual environment
source $HOME/.venvs/universal/bin/activate

# Install dependencies
cd /mnt/torus/projects/universal_transport
pip install -r requirements.txt
```

### Ecosystem Components

This component depends on other Universal LLM Ecosystem packages. Ensure they are accessible in the Python path (they should be in the shared venv or PYTHONPATH).

## Quick Start

### Async Client-Server Communication

```python
import asyncio
from universal_transport import create_unix_server, create_unix_client

# Server with message handler
async def message_handler(message, session):
    print(f"Received: {message}")
    return {"echo": message, "from": "server"}

async def main():
    # Start server
    server = await create_unix_server(
        socket_path="/tmp/my_app.sock",
        message_handler=message_handler
    )
    
    async with server:
        # Create client
        client = await create_unix_client("/tmp/my_app.sock")
        
        async with client:
            # Send large message (works fine, no readline limits!)
            large_data = {"data": "x" * (10 * 1024 * 1024)}  # 10MB message
            response = await client.request_response(large_data, timeout=30.0)
            print(f"Response: {response}")

asyncio.run(main())
```

### process_ipc Migration

```python
# Before (process_ipc with readline buffer limits)
from process_ipc import UnixSocketTransport
transport = UnixSocketTransport(path, limit=10*1024*1024)  # Fails >64KB!

# After (universal_transport v1.0 - no buffer limits)
from universal_transport import create_process_ipc_client
client = await create_process_ipc_client(path)  # Handles 100MB+ easily
```

### Stargate Monitoring Pattern

```python
import asyncio
from universal_transport.specialized import AsyncMonitoringServer, AsyncMonitoringClient

# Proxy side (sending monitoring events)
async def run_proxy():
    async with AsyncMonitoringServer(
        app_name="stargate",
        unix_socket="/tmp/stargate_events.sock"
    ) as server:
        # Send large events reliably (no buffer limits!)
        await server.send_event("chat_completion", {
            "request": {...},   # Full request data  
            "response": {...},  # Full response data
            "metadata": {...}   # Additional context
        })

# GUI side (receiving monitoring events)  
async def run_gui():
    async with AsyncMonitoringClient(
        unix_socket="/tmp/stargate_events.sock"
    ) as client:
        while True:
            event = await client.receive_event(timeout=1.0)
            if event and event.type == 'chat_completion':
                update_gui_panels(event.data)
```

### Serialization Formats

```python
from universal_transport import (
    create_unix_client, JSONSerializer, MessagePackSerializer, RawBinarySerializer
)

# JSON (human-readable, process_ipc compatible)
client_json = await create_unix_client("/tmp/app.sock", serializer=JSONSerializer())

# MessagePack (compact binary, ~40% smaller than JSON)
client_msgpack = await create_unix_client("/tmp/app.sock", serializer=MessagePackSerializer())

# Raw Binary (zero overhead for pre-serialized data)
client_raw = await create_unix_client("/tmp/app.sock", serializer=RawBinarySerializer())
```

### Debug Tools (Replaces JSONL Human-Readability)

```bash
# Real-time protocol monitoring
python -m universal_transport.tools.inspect_protocol monitor /tmp/app.sock --serializer json

# Example output:
# Message 1:
#   Timestamp: 2024-10-24T10:30:56
#   Frame Length: 1,048,580 bytes (1MB message - would fail with readline!)  
#   Payload Length: 1,048,576 bytes
#   Serializer: JSON
#   Message:
#     {
#       "type": "large_data",
#       "data": "..." 
#     }

# Inspect captured protocol files
python -m universal_transport.tools.inspect_protocol inspect capture.bin --serializer json

# Capture sessions for analysis
python -m universal_transport.tools.inspect_protocol capture /tmp/app.sock output.bin --duration 60
```

**Generic Event Types**: The transport layer accepts any string as an event type. Applications define their own event taxonomy (e.g., "chat_completion", "pre_processing", "custom_metric") without validation constraints.

## Project Structure

```
universal_transport/
├── universal_transport/          # Main package
│   ├── __init__.py              # Public API exports
│   ├── core/
│   │   ├── transport/           # Transport implementations
│   │   │   ├── base.py          # Transport ABC
│   │   │   ├── unix.py          # Unix domain sockets
│   │   │   ├── tcp.py           # TCP sockets
│   │   │   └── udp.py           # UDP datagrams
│   │   ├── protocol/            # Protocol implementations
│   │   │   ├── base.py          # Protocol ABC
│   │   │   ├── json_lines.py    # JSON Lines framing
│   │   │   ├── signal_payload.py # process_ipc compatibility
│   │   │   └── envelope.py      # LLM Gateway envelopes
│   │   └── client_server/       # Client/Server patterns
│   │       ├── client.py        # Generic client
│   │       └── server.py        # Generic server
│   ├── specialized/             # Ecosystem-specific patterns
│   │   └── monitoring.py        # Stargate monitoring transport
│   └── adapters/                # Integration adapters
│       └── event_bus.py         # universal_event_bus bridge
├── tests/                       # Test suite
├── examples/                    # Usage examples
└── docs/                        # Documentation
```

## Design Principles

1. **Composability**: Mix any Transport with any Protocol
2. **Ecosystem Compatibility**: Support existing Universal project patterns
3. **Performance**: Unix sockets for speed, reliable protocols for large payloads
4. **Extensibility**: Easy to add new transports (WebSocket, gRPC, etc.)
5. **PYTHONPATH Deployment**: Simple import, no installation complexity

## Primary Use Case

**Stargate Monitoring Transport**: Modern async transport that eliminates:
- **readline buffer limits**: No more `LimitOverrunError` for large events  
- **UDP size limits**: No more `[Errno 90] Message too long`
- **Packet loss**: Reliable Unix socket delivery
- **Complex sync code**: Clean async API

## Architecture Benefits

### Why Length-Prefixed Protocol?

| Benefit | Description |
|---------|------------|
| **No Buffer Limits** | Uses `readexactly()` instead of `readline()` - handles 100MB+ messages |
| **Binary Safe** | No delimiter conflicts with payload content |
| **High Performance** | No buffer scanning overhead |
| **Deterministic** | Always know exactly how many bytes to read |
| **Simple** | Fixed 4-byte overhead per message |

### The Problem with JSONL (Why We Removed It)

```python
# ❌ JSONL (removed in v1.0) - would fail with large messages
line = await reader.readline()  # 64KB buffer limit!
# Error: LimitOverrunError: chunk is longer than limit

# ✅ Length-prefixed (v1.0) - handles multi-MB messages
length_bytes = await reader.readexactly(4)  # No buffer scanning
payload = await reader.readexactly(length)  # Exact read, no limits
```

### Key Architectural Insight
> **"The best way to avoid readline buffer limits is to never use readline for binary protocols."**

## Documentation

- [Framing Architecture](docs/FRAMING_ARCHITECTURE.md) - Length-prefixed design and readline pitfalls **[START HERE]**
- [API Reference](docs/API_REFERENCE.md) - Complete API documentation  
- [Migration Guide](docs/MIGRATION_GUIDE.md) - Migrating from process_ipc and JSONL systems

## Testing

Run the test suite:

```bash
# Unit tests
pytest tests/

# With coverage
pytest tests/ --cov=universal_transport --cov-report=html

# Integration tests
pytest tests/integration/
```

## Examples

See the `examples/` directory for:
- `async_length_prefixed_example.py` - Async client-server with large payloads
- `serialization_formats_example.py` - Compare JSON, MessagePack, etc.
- `stargate_simulation.py` - Stargate GUI ↔ Proxy communication (async)
- `stargate_migration_example.py` - Migration from legacy to async API

## Author

**krunch3r76** ([@krunch3r76](https://github.com/krunch3r76))

- GitHub: [@krunch3r76](https://github.com/krunch3r76)
- Email: biz@u26a4.com

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development guidelines and setup instructions.
