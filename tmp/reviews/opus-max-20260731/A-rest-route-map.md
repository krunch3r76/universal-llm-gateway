# REST / UDS route map — Workstream A early deliverable

**Probed:** 2026-07-31T20:57:53Z · host local
**Sources:** `libs/transport_utils/client_factory.py`, service configs under `services/` + `scripts/model_manager/ui/controller/service_config.py`, live `curl`/httpx probes.
**Do not guess ports** — every TCP port below was either configured in-tree or observed listening.

| Service | Address | Serves `/openapi.json`? | Paths | Schemas | Live now? | Notes |
|---|---|---|---:|---:|---|---|
| cortex-api (TCP sibling) | `http://127.0.0.1:8202` | yes | 82 | 118 | yes | cortex-api |
| cortex-api (UDS) | `unix:///tmp/universal-protocol/cortex-api.sock` | yes | 82 | 118 | yes | cortex-api |
| git_integration_worker | `http://127.0.0.1:8091/api/v1/git/openapi.json` | yes | 20 | 16 | yes | git-integration-worker |
| stargate | `http://127.0.0.1:9999` | yes | 102 | 32 | yes | Universal LLM Gateway - Stargate Proxy |
| gateway | `http://127.0.0.1:9998` | no/fail | — | — | no | manage status=running but TCP :9998 connection refused this probe; no gateway.sock found |
| agent-bus | `unix:///tmp/universal-protocol/agent-bus.sock` | yes | 24 | 40 | yes | agent-bus |
| rag | `unix:///tmp/universal-protocol/rag.sock` | yes | 28 | 52 | yes | RAG Service |
| event-store (query UDS) | `unix:///tmp/universal-protocol/events-query.sock` | yes | 3 | 0 | yes | Event Store |
| event-store (query TCP) | `http://127.0.0.1:7102` | yes | 3 | 0 | yes | Event Store |
| event-store (ingest TCP) | `http://127.0.0.1:7101` | no/fail | — | — | no | ingest port; openapi probe timed out / not a docs surface |
| cloud-proxy | `unix:///tmp/universal-protocol/cloud-proxy.sock` | yes | 22 | 2 | yes | Cloud Proxy |
| email-bridge | `unix:///tmp/universal-protocol/email-bridge.sock` | yes | 21 | 36 | yes | email-bridge |
| sms-bridge | `unix:///tmp/universal-protocol/sms-bridge.sock` | yes | 10 | 7 | yes | sms-bridge |
| journal-bridge | `http://127.0.0.1:8200` | yes | 8 | 13 | yes | journal-bridge |
| agent-bus-ui-proxy | `http://127.0.0.1:7779` | yes | 2 | 2 | yes | agent-bus-ui-proxy |
| cdp-ask | `http://127.0.0.1:8770` | no/fail | — | — | no | manage status=running but :8770 refused / no cdp-ask.sock; default --port 8770 in services/cdp-ask/main.py |
| manage | `unix:///tmp/universal-protocol/manage.sock` | no | — | — | yes | JSON-RPC line protocol (not OpenAPI); status OK |

## Derivation notes

- **cortex-api** dual-bind: UDS `CORTEX_API_SOCK` + TCP sibling `CORTEX_API_HTTP_PORT` (default **8202**). Both returned identical OpenAPI (82 paths / 118 schemas) this probe.
- **git_integration_worker** OpenAPI is under `/api/v1/git/openapi.json`, not root `/openapi.json` (root 404).
- **gateway** is reported running by manage but did not accept TCP on `:9998` this probe; no UDS sibling found under `/tmp/universal-protocol/`.
- **cdp-ask** default port 8770; manage claims running but endpoint not reachable this probe (likely different bind or crashed after status snapshot).
- **manage** is Class C — JSON-RPC over UDS, not HTTP/OpenAPI.

## client_factory defaults (code)

```
RAG_SOCKET_PATH=/tmp/universal-protocol/rag.sock
CORTEX_API_SOCK=/tmp/universal-protocol/cortex-api.sock
AGENT_BUS_SOCK=/tmp/universal-protocol/agent-bus.sock
STARGATE → http://localhost:9999 (or STARGATE_URL / STARGATE_UNIX_SOCKET)
EVENTS_QUERY_SOCK=/tmp/universal-protocol/events-query.sock
MANAGE_SOCKET=/tmp/universal-protocol/manage.sock
CLOUD_PROXY_SOCKET_PATH=/tmp/universal-protocol/cloud-proxy.sock
EMAIL_BRIDGE_SOCK=/tmp/universal-protocol/email-bridge.sock
SMS_BRIDGE_SOCK=/tmp/universal-protocol/sms-bridge.sock
```
