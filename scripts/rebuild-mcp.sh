#!/usr/bin/env bash
# Rebuild MCP server image and start the container.
# Reads MCP_PROJECT_DIR, MCP_AUTH_TOKEN, etc. from ~/.gateway/mcp.yaml.
# Usage: ./scripts/rebuild-mcp.sh [--use-cache] [from repo root or any subdir]

set -e

usage() {
  cat <<'EOF'
Usage: ./scripts/rebuild-mcp.sh [--use-cache]

Options:
  --use-cache  Rebuild with normal Docker layer cache instead of --no-cache.
  -h, --help   Show this help text.
EOF
}

USE_CACHE=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --use-cache)
      USE_CACHE=true
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
mcp_url = (cfg.get('mcp_server_url') or '').strip()
if mcp_url:
    print('export MCP_SERVER_URL=' + shlex.quote(mcp_url))
fp = (cfg.get('firefox_profile_dir') or '').strip()
if fp:
    print('export FIREFOX_PROFILE_DIR=' + shlex.quote(str(Path(fp).expanduser())))
")"

cd "$WORKSPACE_ROOT"
COMPOSE_ARGS=(-f docker/compose/mcp-server.yml)
if [[ "$ENABLE_BROWSER_TOOLS" == "true" ]]; then
  COMPOSE_ARGS+=(-f docker/compose/mcp-server-browser.override.yml)
fi

BUILD_ARGS=(build mcp-server)
if [[ "$USE_CACHE" == "true" ]]; then
  echo "Building MCP server (using cache)..."
else
  echo "Building MCP server (no cache)..."
  BUILD_ARGS=(build --no-cache mcp-server)
fi
docker compose "${COMPOSE_ARGS[@]}" "${BUILD_ARGS[@]}"

echo "Starting MCP server..."
docker compose "${COMPOSE_ARGS[@]}" up -d mcp-server

echo "Done. Check: docker ps --filter name=mcp-server"
