#!/usr/bin/env bash
# Sync MCP source into the container, restart, and wait for healthy.
# Not a true Docker rebuild — pip deps/Dockerfile changes still need ./manage.
# Reads MCP_PROJECT_DIR, MCP_AUTH_TOKEN, etc. from ~/.gateway/mcp.yaml.
# Usage: ./scripts/sync-and-restart-mcp.sh [--no-cache] [from repo root or any subdir]

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./scripts/sync-and-restart-mcp.sh [--no-cache]

Options:
  --no-cache   Force full rebuild without cache (pulls fresh base images).
  -h, --help   Show this help text.
EOF
}

NO_CACHE=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-cache)
      NO_CACHE=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

MCP_YAML="${MCP_YAML:-$HOME/.gateway/mcp.yaml}"
if [[ ! -f "$MCP_YAML" ]]; then
  echo "MCP config not found: $MCP_YAML" >&2
  exit 1
fi

# Export env vars from mcp.yaml (matches build_mcp_env from service_config)
eval "$(python3 -c "
import os, shlex, yaml
from pathlib import Path
cfg = yaml.safe_load(Path(os.environ.get('MCP_YAML', str(Path.home() / '.gateway/mcp.yaml'))).read_text()) or {}
def parse_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return bool(value)
token = ''
token_env = (cfg.get('auth_token_env') or '').strip()
if token_env:
    token = (os.environ.get(token_env) or '').strip()
token = token or (cfg.get('auth_token') or '').strip()
if not token:
    print('auth_token not resolved (set auth_token_env env var or auth_token in MCP config)', file=__import__('sys').stderr)
    raise SystemExit(1)
project_dir = (cfg.get('project_dir') or '').strip()
if not project_dir:
    print('project_dir missing or empty in MCP config', file=__import__('sys').stderr)
    raise SystemExit(1)
tasks_dir = (cfg.get('tasks_dir') or '').strip() or f'{project_dir}/tasks'
data_dir = str(Path(cfg.get('data_dir', '~/mcp-data')).expanduser())
print('export MCP_AUTH_TOKEN=' + shlex.quote(token))
print('export MCP_PROJECT_DIR=' + shlex.quote(project_dir))
print('export MCP_TASKS_DIR=' + shlex.quote(tasks_dir))
print('export MCP_DATA_DIR=' + shlex.quote(data_dir))
print('export ENABLE_BROWSER_TOOLS=' + shlex.quote('true' if cfg.get('enable_browser_tools') else 'false'))
print(
    'export REFRESH_CURSOR_DESCRIPTORS_AFTER_REBUILD='
    + shlex.quote('true' if parse_bool(cfg.get('refresh_cursor_descriptors_after_rebuild', False)) else 'false')
)
print('export ENABLE_CONTEXT_TOOLS=true')
project_access = (cfg.get('project_access') or 'ro').strip().lower()
if project_access == 'rw':
    print('export MCP_PROJECT_MOUNT_MODE=rw')
    print('export PROJECT_READ_ONLY=false')
else:
    print('export MCP_PROJECT_MOUNT_MODE=ro')
    print('export PROJECT_READ_ONLY=true')
tasks = (cfg.get('tasks_access') or 'ro').strip().lower()
if tasks == 'rw':
    print('export MCP_TASKS_MOUNT_MODE=rw')
    print('export TASKS_READ_ONLY=false')
else:
    print('export MCP_TASKS_MOUNT_MODE=ro')
    print('export TASKS_READ_ONLY=true')
brave = (cfg.get('BRAVE_SEARCH_API_KEY') or cfg.get('brave_search_api_key') or '').strip()
if brave:
    print('export BRAVE_SEARCH_API_KEY=' + shlex.quote(brave))
bridge = (cfg.get('BRIDGE_TOKEN') or cfg.get('bridge_token') or '').strip()
if bridge:
    print('export BRIDGE_TOKEN=' + shlex.quote(bridge))
agent_bus = (cfg.get('AGENT_BUS_TOKEN') or cfg.get('agent_bus_token') or '').strip()
if agent_bus:
    print('export AGENT_BUS_TOKEN=' + shlex.quote(agent_bus))
anthropic = (cfg.get('ANTHROPIC_API_KEY') or cfg.get('anthropic_api_key') or os.environ.get('ANTHROPIC_API_KEY') or '').strip()
if anthropic:
    print('export ANTHROPIC_API_KEY=' + shlex.quote(anthropic))
openai = (cfg.get('OPENAI_API_KEY') or cfg.get('openai_api_key') or os.environ.get('OPENAI_API_KEY') or '').strip()
if openai:
    print('export OPENAI_API_KEY=' + shlex.quote(openai))
xai = (cfg.get('XAI_API_KEY') or cfg.get('xai_api_key') or os.environ.get('XAI_API_KEY') or '').strip()
if xai:
    print('export XAI_API_KEY=' + shlex.quote(xai))
google = (cfg.get('GOOGLE_API_KEY') or cfg.get('google_api_key') or os.environ.get('GOOGLE_API_KEY') or '').strip()
if google:
    print('export GOOGLE_API_KEY=' + shlex.quote(google))
for key in (
    'CLAUDEBURST_HOST',
    'CLAUDEBURST_PORT',
    'CLAUDEBURST_PERPS_HOST',
    'CLAUDEBURST_PERPS_PORT',
    'CLAUDEBURST_COINBASE_HOST',
    'CLAUDEBURST_COINBASE_PORT',
):
    value = (cfg.get(key) or os.environ.get(key) or '').strip()
    if value:
        print(f'export {key}=' + shlex.quote(str(value)))
mcp_url = (cfg.get('mcp_server_url') or '').strip()
if mcp_url:
    print('export MCP_SERVER_URL=' + shlex.quote(mcp_url))
    # MCP_PUBLIC_URL is the canonical env name used by libs/llm_adapters/_mcp_entry.resolve_mcp_env;
    # kept in sync with MCP_SERVER_URL so both strict (adapter) and existing callers resolve.
    print('export MCP_PUBLIC_URL=' + shlex.quote(mcp_url))
fp = (cfg.get('firefox_profile_dir') or '').strip()
if fp:
    print('export FIREFOX_PROFILE_DIR=' + shlex.quote(str(Path(fp).expanduser())))
web_fetcher = (cfg.get('WEB_FETCHER_URL') or cfg.get('web_fetcher_url') or '').strip()
if web_fetcher:
    print('export WEB_FETCHER_URL=' + shlex.quote(web_fetcher))
")"

cd "$WORKSPACE_ROOT"
COMPOSE_ARGS=(-f docker/compose/mcp-server.yml)
if [[ "$ENABLE_BROWSER_TOOLS" == "true" ]]; then
  COMPOSE_ARGS+=(-f docker/compose/mcp-server-browser.override.yml)
fi

cleanup_orphan_mcp_containers() {
  # `docker compose up` recreate-on-image-change can leave behind a rename-pattern
  # orphan (e.g. `d1e48bbeb4e3_mcp-server`) in Created state when the rename
  # succeeds but the subsequent create step fails. These orphans then block
  # future `up` attempts. Purge any such leftovers before attempting the up.
  local orphans
  orphans="$(
    docker ps -a --format '{{.Names}}' 2>/dev/null \
      | grep -E '^[a-f0-9]+_mcp-server$' || true
  )"
  if [[ -n "$orphans" ]]; then
    echo "Removing orphan mcp-server containers: $(echo "$orphans" | tr '\n' ' ')"
    echo "$orphans" | xargs -r docker rm -f >/dev/null 2>&1 || true
  fi
}

wait_for_mcp_healthy() {
  local timeout_s="${1:-90}"
  local start_ts status
  start_ts="$(date +%s)"
  while true; do
    status="$(
      docker inspect \
        -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
        mcp-server 2>/dev/null || true
    )"
    case "$status" in
      healthy|running)
        return 0
        ;;
      unhealthy|exited|dead)
        echo "WARNING: MCP server entered state '${status}'." >&2
        return 1
        ;;
    esac
    if (( $(date +%s) - start_ts >= timeout_s )); then
      echo "WARNING: Timed out waiting for MCP server to become healthy." >&2
      return 1
    fi
    sleep 2
  done
}

if [[ "$NO_CACHE" == "true" ]]; then
  echo "Building MCP server (no cache, pulling fresh base images)..."
  bash docker/scripts/build/build-mcp.sh --no-cache
else
  echo "Building MCP server (cached, refreshing source layers)..."
  bash docker/scripts/build/build-mcp.sh --refresh-source
fi

cleanup_orphan_mcp_containers

echo "Starting MCP server..."
docker compose "${COMPOSE_ARGS[@]}" up -d mcp-server

if wait_for_mcp_healthy; then
  if [[ "${REFRESH_CURSOR_DESCRIPTORS_AFTER_REBUILD}" == "true" ]]; then
    REFRESH_SCRIPT="$WORKSPACE_ROOT/scripts/refresh-cursor-mcp-descriptors"
    if [[ -f "$REFRESH_SCRIPT" ]]; then
      echo "Refreshing Cursor MCP descriptors..."
      if ! bash "$REFRESH_SCRIPT"; then
        echo "WARNING: Cursor MCP descriptor refresh failed." >&2
      fi
    else
      echo "WARNING: Cursor descriptor refresh is enabled, but script is missing: $REFRESH_SCRIPT" >&2
    fi
  fi
else
  echo "WARNING: Skipping descriptor refresh because MCP server is not healthy." >&2
fi

echo "Done. Check: docker ps --filter name=mcp-server"
