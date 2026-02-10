# Universal Logging

A production-ready, auto-initializing logging framework with JSON-only output.

## Features

- **Zero-Configuration Setup**: Import and use — no manual configuration
- **JSON-Only Output**: All logs are valid NDJSON (machine-parseable)
- **Canonical Schema**: Single schema, multiple renderers (compact, pretty, colorized)
- **Truncation Support**: Configurable field truncation for large payloads
- **Environment Overrides**: `LOG_LEVEL`, `UNIVERSAL_LOG_PRETTY`, `UNIVERSAL_LOG_COLOR`

## Quick Start

```python
from universal_logging import get_logger

logger = get_logger(__name__)
logger.info("Hello, world!")
logger.error("An error occurred", exc_info=True)
```

Output (NDJSON):
```json
{"@timestamp":"2026-01-24T10:30:45.123Z","level":"INFO","logger":"myapp","message":"Hello, world!","caller":{"file":"app.py","func":"main","line":42},"process":12345,"thread":"MainThread"}
```

## Configuration

### YAML Configuration

```yaml
formatters:
  json:
    class: universal_logging.renderers.JSONFormatter
    truncate: true
    max_field_size: 2000

handlers:
  console:
    class: logging.StreamHandler
    formatter: json
    stream: ext://sys.stdout

root:
  level: INFO
  handlers: [console]
```

### Environment Variables

| Variable | Effect |
|----------|--------|
| `LOG_LEVEL` | Override log level (DEBUG, INFO, WARNING, ERROR) |
| `UNIVERSAL_LOG_PRETTY` | Enable indented JSON (set to `1`) |
| `UNIVERSAL_LOG_COLOR` | Enable ANSI colors (set to `1`) |

## Schema

All log records conform to this schema:

| Field | Type | Description |
|-------|------|-------------|
| `@timestamp` | string | ISO 8601 with milliseconds, UTC |
| `level` | string | DEBUG, INFO, WARNING, ERROR, CRITICAL |
| `logger` | string | Logger name |
| `message` | string | Log message |
| `caller` | object | `{file, func, line}` |
| `error` | object | `{type, message, traceback}` (only if exception) |
| `extra` | object | User-provided fields |
| `process` | int | Process ID |
| `thread` | string | Thread name |

## Renderers

| Renderer | Output | Use Case |
|----------|--------|----------|
| `CompactJSONRenderer` | Single-line NDJSON | Production, log aggregators |
| `PrettyJSONRenderer` | Indented JSON | Development |
| `ColorizedJSONRenderer` | ANSI-colored JSON | Interactive terminal |

**Invariant**: `strip_ansi(any_renderer.render(record)) == compact_json(record)`

## Viewing Logs

See `scripts/log_viewer.py` for a colorized log viewer:

```bash
# Live tail with colors
python scripts/log_viewer.py --tail /tmp/logs/gateway.log

# Filter by level
python scripts/log_viewer.py --tail --level ERROR /tmp/logs/gateway.log

# Quick jq one-liner
tail -f logs.ndjson | jq -C '"\(.["@timestamp"]) \(.level) [\(.logger)] \(.message)"'
```

## Migration from v2.x

| Old | New |
|-----|-----|
| `EnhancedFormatter` | `JSONFormatter` |
| `ColoredFormatter` | `JSONFormatter` (set `UNIVERSAL_LOG_COLOR=1`) |
| `create_colored_logger()` | `get_logger()` |
| Text format strings | Remove from config (ignored) |

## Ecosystem Integration

Part of the **Universal LLM Ecosystem**:
- universal-llm-gateway
- universal-stargate
- universal-event-bus
- process_ipc

All components share `$HOME/.venvs/universal` and use NDJSON logging.

## License

MIT License - see [LICENSE](LICENSE) for details.
