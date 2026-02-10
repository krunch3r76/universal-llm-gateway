# Universal Event Bus

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Event-driven messaging and coordination infrastructure for the Universal LLM Ecosystem.

## Description

Universal Event Bus is a lightweight, asynchronous event messaging system that provides publish-subscribe patterns and inter-process communication for the Universal LLM Ecosystem. It enables loosely-coupled, event-driven architecture across ecosystem components.

### Key Features

- **Asynchronous Event Bus**: Thread-safe publish-subscribe event distribution
- **Event Management**: Type-safe event creation with timestamps and metadata
- **Network Transport**: UDP-based transport for inter-process event communication
- **Monitoring Support**: Built-in monitoring message schemas for observability
- **Debug Broadcasting**: Minimal event debug broadcaster for development and troubleshooting
- **Ecosystem Integration**: Designed for universal_logging and other ecosystem components

### Architecture

- **EventBus**: Central event distribution hub with topic-based subscriptions
- **Event**: Dataclass-based event representation with timestamps
- **UDPTransport**: Network transport layer for event serialization and transmission
- **UDPBridge**: High-level bridge for connecting event buses across processes
- **Monitoring**: Message schemas for system observability

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

**Important**: The shared virtual environment `$HOME/.venvs/universal` is used across all ecosystem components (universal-llm-gateway, universal-stargate, universal_logging, universal_event_bus, etc.). This ensures consistent Python versions and allows ecosystem components to import each other.

### Install Dependencies

```bash
pip install -r requirements.txt
```

**Note**: This project has no external PyPI dependencies. The `requirements.txt` is minimal and documents that ecosystem dependencies (like `universal_logging`) are expected in the Python path.

### Ecosystem Components

This component depends on other Universal LLM Ecosystem packages. Ensure they are accessible in the Python path (they should be in the shared venv or PYTHONPATH):

- `universal_logging` - Structured logging infrastructure

## Usage

### Creating Events with Factory Functions (Required)

**IMPORTANT**: All events MUST be created using factory functions decorated with `@event_factory`. Direct `Event()` construction is forbidden and will raise `RuntimeError`.

```python
from universal_event_bus import Event, event_factory

# Define factory function with decorator
@event_factory
def UserLoggedIn(user_id: str, session_id: str) -> Event:
    """Create USER_LOGGED_IN event."""
    return Event(
        signal="UserLoggedIn",
        payload={
            "user_id": user_id,
            "session_id": session_id
        }
    )

# Usage
event = UserLoggedIn(user_id="123", session_id="abc")  # ✅ Works
await bus.publish_async_nowait(event)

# Direct construction is forbidden
event = Event(signal="UserLoggedIn", payload={...})  # ❌ RuntimeError!
```

**Why Factory Functions?**
- Type-safe event creation with clear parameters
- Consistent payload structure
- Discoverable API (IDE autocomplete)
- Runtime enforcement prevents misuse

### Basic Event Bus

```python
from universal_event_bus import EventBus, Event, event_factory

# Create event bus
bus = EventBus()

# Define event factory
@event_factory
def MyEvent(message: str) -> Event:
    return Event(
        signal="my.event.topic",
        payload={"message": message}
    )

# Subscribe to events
async def handle_event(event: Event):
    print(f"Received: {event.signal} - {event.payload}")

bus.subscribe_async("my.event.topic", handle_event)

# Publish event using factory
event = MyEvent(message="Hello, World!")
await bus.publish_async_nowait(event)
```

### UDP Bridge for Inter-Process Communication

```python
from universal_event_bus import UDPBridge, EventBus

# Process A
bus_a = EventBus()
bridge_a = UDPBridge(bus_a, local_port=5001, remote_port=5002)
await bridge_a.start()

# Process B
bus_b = EventBus()
bridge_b = UDPBridge(bus_b, local_port=5002, remote_port=5001)
await bridge_b.start()

# Events published on bus_a are transmitted to bus_b and vice versa
```

### Debug Broadcasting

```python
from universal_event_bus import MinimalEventDebugBroadcaster

# Create debug broadcaster (sends events to debug clients)
debug_broadcaster = MinimalEventDebugBroadcaster(
    host="localhost",
    port=9998
)
await debug_broadcaster.start()

# Subscribe to event bus
bus.subscribe("debug.events.*", debug_broadcaster.broadcast_event)
```

## Project Structure

```
universal_event_bus/
├── events/
│   ├── event_bus.py          # EventBus publish-subscribe implementation
│   ├── event.py               # Event dataclass and utilities
│   └── debug_broadcaster.py   # Debug event broadcasting
├── transports/
│   └── udp_transport.py       # UDP network transport layer
├── bridges/
│   └── udp_bridge.py          # High-level UDP bridge for event buses
├── monitoring/
│   └── message_schemas.py     # Monitoring message schemas
├── pyproject.toml             # Project metadata and tool configuration
├── requirements.txt           # External dependencies (minimal)
└── LICENSE                    # MIT License
```

## Development

### IDE Setup

This project includes IDE configuration for consistent development experience:

- **Cursor/VS Code**: `.vscode/settings.json` configures Ruff (formatting/linting) and BasedPyright (type checking)
- **Required Extensions**:
  - Ruff (charliermarsh.ruff)
  - BasedPyright (detachhead.basedpyright)

### Code Standards

- **Python**: 3.12+
- **Formatting**: Ruff (88 character line length, double quotes)
- **Linting**: Ruff (pycodestyle, pyflakes, isort, pep8-naming, pyupgrade)
- **Type Checking**: BasedPyright

### Running Tests

```bash
# Activate shared virtual environment
source $HOME/.venvs/universal/bin/activate

# Run tests (if pytest is installed)
pytest

# Run with coverage
pytest --cov=universal_event_bus
```

## Ecosystem Context

Universal Event Bus is part of the **Universal LLM Ecosystem**, a collection of interdependent components for building AI-powered applications:

- **universal-llm-gateway**: Backend inference service
- **universal-stargate**: API proxy and routing
- **universal_logging**: Structured logging infrastructure
- **universal_event_bus**: Event-driven messaging (this project)
- **universal_transport**: Socket and transport abstractions
- **universal_protocol**: Application-layer communication patterns
- **process_ipc**: Process lifecycle and health monitoring
- **inference_djinn**: Inference process management

All ecosystem components share the same virtual environment and development standards.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development guidelines and setup instructions.

## Author

**krunch3r76** ([@krunch3r76](https://github.com/krunch3r76))

- GitHub: [@krunch3r76](https://github.com/krunch3r76)
- Email: biz@u26a4.com

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

