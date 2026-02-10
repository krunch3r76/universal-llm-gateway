# Process IPC

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Description

**Process IPC** is a robust inter-process communication library designed for the Universal LLM Ecosystem. It provides a comprehensive framework for managing supervisor-worker process architectures with features including:

- **Process Lifecycle Management**: Supervisor-worker pattern with health monitoring and crash detection
- **IPC Communication**: Message-based communication with serialization support
- **State Management**: Centralized state tracking for both supervisor and worker processes
- **Resource Monitoring**: Built-in CPU, memory, and optional GPU monitoring
- **Orphan Prevention**: Parent death signal handling to prevent orphaned processes
- **Type-Safe Messaging**: Schema validation and type-safe message handling
- **Signal Management**: Cross-platform signal handling with POSIX support

This component is part of the Universal LLM Ecosystem and is used by other ecosystem components for reliable process management and inter-process communication.

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

**Important**: The shared virtual environment `$HOME/.venvs/universal` is used across all ecosystem components (universal-llm-gateway, universal-stargate, universal_logging, universal-event-bus, etc.). This ensures consistent Python versions and allows ecosystem components to import each other.

### Install Dependencies

```bash
pip install -r requirements.txt
```

### Ecosystem Components

This component depends on other Universal LLM Ecosystem packages. Ensure they are accessible in the Python path (they should be in the shared venv or PYTHONPATH).

## Features

### Core Components

- **Supervisor Process** (`process/supervisor.py`): Manages worker processes, handles lifecycle, and monitors health
- **Worker Process** (`process/worker.py`): Executes tasks and communicates with supervisor
- **State Management** (`core/state_manager.py`): Centralized state tracking with thread-safe operations
- **Message Handling** (`core/messages.py`): Type-safe message definitions and serialization
- **Signal Management** (`core/signals.py`): Cross-platform signal handling and factory functions

### Key Features

- **Health Monitoring**: Automatic health checks with configurable intervals
- **Crash Detection**: Immediate detection and handling of worker crashes
- **Resource Monitoring**: Track CPU, memory, and GPU usage (optional)
- **Schema Validation**: Type-safe message validation using schemas
- **Async Support**: Full async/await support for modern Python applications
- **Parent Death Detection**: Automatic cleanup when parent process terminates
- **IPC Signal Factory Functions**: Type-safe factory functions enforce payload structure consistency across process boundaries

## IPC Signal Factory Functions

All IPC signals with structured payloads have factory functions for type safety and consistency across process boundaries.

### Why Factory Functions?

IPC signals cross process boundaries where runtime type checking cannot help. Without factories:
- Field name mismatches (e.g., `connected` vs `connectivity`) break handlers silently
- No IDE autocomplete for payload fields
- Refactoring requires manual search across processes
- Payload structure is undocumented

### Usage

```python
from process_ipc import Ready, ProcessCrashDetected, StateReport

# Create typed IPC messages
ready_event = Ready(
    worker_id="model-123",
    status="loaded",
    worker_status={"healthy": True},
)

# Factory documents payload structure
crash_event = ProcessCrashDetected(
    process_id="worker-1",
    error_message="Segmentation fault",
    exit_code=-11,
    pid=12345,
    socket_path="/tmp/worker.sock",
    stderr="Core dumped",
    is_signal_termination=True,
    signal_name="SIGSEGV",
)

# State reporting
state_report = StateReport(
    worker_id="model-123",
    state="processing",
    details={"progress": 0.75},
)
```

### Available Factory Functions

All signals with structured payloads have factory functions:

- `Ready()` - Worker initialization complete
- `StateReport()` - Worker state update
- `ActivityReport()` - Worker activity notification
- `ProgressReport()` - Long-running operation progress
- `CapabilitiesReport()` - Worker capability advertisement
- `HealthResponse()` - Health check response
- `ShutdownAck()` - Shutdown acknowledgment
- `CommandError()` - Command execution error
- `StreamStarted()` - Stream initiation confirmation
- `StreamChunk()` - Stream data chunk
- `StreamEnd()` - Stream completion
- `StreamError()` - Stream error
- `StreamCancelled()` - Stream cancellation
- `Error()` - General error signal
- `ProcessCrashDetected()` - Process crash notification (Event-based, not IPC transport)

## Usage Examples

See the `examples/` directory for comprehensive demonstrations:

- `simple_worker_demo.py`: Basic supervisor-worker communication
- `crash_detection_demo.py`: Handling worker crashes and restarts
- `resource_monitoring_demo.py`: Monitor process resource usage
- `async_streaming_demo.py`: Async message streaming
- `orphan_prevention_demo.py`: Parent death signal handling
- `schema_usage_example.py`: Type-safe message validation

## Architecture

Process IPC uses a modular architecture with clear separation of concerns:

- **Core Layer**: Basic types, interfaces, and message definitions
- **Process Layer**: Supervisor and worker implementations
- **Services Layer**: Bootstrap, logging, and resource monitoring
- **Transport Layer**: Communication abstractions (extensible)

## Author

**krunch3r76** ([@krunch3r76](https://github.com/krunch3r76))

- GitHub: [@krunch3r76](https://github.com/krunch3r76)
- Email: biz@u26a4.com

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
