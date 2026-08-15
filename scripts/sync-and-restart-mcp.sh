#!/usr/bin/env bash
# Sync MCP Python source into the running container and restart (no image rebuild).
# Pass --no-cache only for pip/Dockerfile/base-image changes (full build-mcp.sh rebuild).
# ¬ curl x.ai/cli/install.sh here — that installer symlinks both grok and agent into
#   ~/.grok/bin (and often ~/.local/bin), which can overwrite the Cursor agent binary.
# Reads MCP_PROJECT_DIR, MCP_AUTH_TOKEN, etc. from ~/.gateway/mcp.yaml.
# Routine path: docker cp into /app + docker stop/start (preserves writable layer).
# ¬ compose up -d after sync — recreate wipes docker cp (friction 6538).
# Usage: ./scripts/sync-and-restart-mcp.sh [--no-cache] [from repo root or any subdir]

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./scripts/sync-and-restart-mcp.sh [--no-cache]

Options:
  --no-cache   Full image rebuild (pip/Dockerfile/base-image changes — rare).
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
print('export ENABLE_CONTEXT_TOOLS=' + shlex.quote('false' if cfg.get('enable_context_tools') is False else 'true'))
# Full read/write by default; mounts are rw and writes are unconditional.
print('export MCP_PROJECT_MOUNT_MODE=rw')
print('export MCP_TASKS_MOUNT_MODE=rw')
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
project_ask = (cfg.get('PROJECT_ASK_URL') or cfg.get('project_ask_url') or '').strip()
if project_ask:
    print('export PROJECT_ASK_URL=' + shlex.quote(project_ask))

def _export_x_credential(key, value):
    if value:
        print(f'export {key}=' + shlex.quote(value))

for _x_key in ('X_API_KEY', 'X_API_SECRET', 'X_ACCESS_TOKEN', 'X_ACCESS_SECRET'):
    _x_val = (cfg.get(_x_key) or os.environ.get(_x_key) or '').strip()
    _export_x_credential(_x_key, _x_val)

_x_env_file = (cfg.get('x_account_env_file') or '').strip()
if not _x_env_file:
    _default_x_env = Path('/mnt/torus/projects/xpharmdbot/.env')
    if _default_x_env.is_file():
        _x_env_file = str(_default_x_env)
if _x_env_file:
    _x_path = Path(_x_env_file).expanduser()
    if _x_path.is_file():
        for _line in _x_path.read_text().splitlines():
            _line = _line.strip()
            if not _line or _line.startswith('#'):
                continue
            if _line.startswith('export '):
                _line = _line[7:]
            if '=' not in _line:
                continue
            _k, _, _v = _line.partition('=')
            _k = _k.strip()
            if _k not in ('X_API_KEY', 'X_API_SECRET', 'X_ACCESS_TOKEN', 'X_ACCESS_SECRET'):
                continue
            if (cfg.get(_k) or os.environ.get(_k) or '').strip():
                continue
            _v = _v.strip()
            if len(_v) >= 2 and _v[0] == _v[-1] and _v[0] in (chr(34), chr(39)):
                _v = _v[1:-1]
            _export_x_credential(_k, _v)
")"

cd "$WORKSPACE_ROOT"
COMPOSE_ARGS=(-f docker/compose/mcp-server.yml)
if [[ "$ENABLE_BROWSER_TOOLS" == "true" ]]; then
  COMPOSE_ARGS+=(-f docker/compose/mcp-server-browser.override.yml)
fi

purge_mcp_compose_orphans() {
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

remove_mcp_container_for_image_recreate() {
  purge_mcp_compose_orphans
  # After a full image rebuild the existing mcp-server container (if any) must
  # be removed before `docker compose up` attempts to create a new one — compose
  # performs an internal rename-then-create that fails with a "name already in
  # use" conflict when the old container is still present and the daemon cannot
  # atomically swap it. NOT used on the routine sync path: docker cp into /app
  # lives in the container writable layer and must survive stop→start.
  if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx 'mcp-server'; then
    echo "Removing existing mcp-server container before image recreate..."
    docker rm -f mcp-server >/dev/null 2>&1 || true
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

write_source_sync_stamp() {
  local c=mcp-server
  local stamp code_sha working_tree_state status_output
  stamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  code_sha="$(git -C "$WORKSPACE_ROOT" rev-parse HEAD)"
  if status_output="$(git -C "$WORKSPACE_ROOT" status --porcelain --untracked-files=all 2>/dev/null)"; then
    if [[ -n "${status_output}" ]]; then
      working_tree_state="dirty"
    else
      working_tree_state="clean"
    fi
  else
    working_tree_state="unknown"
  fi
  docker exec -u 0 "$c" sh -c \
    "printf '%s\n%s\nsource_basis=working_tree\ncode_version_semantics=checkout_head_at_source_sync\nworking_tree_state=${working_tree_state}\n' '${stamp}' '${code_sha}' > /app/.source_sync_stamp && chown mcp:mcp /app/.source_sync_stamp"
  echo "Wrote source sync stamp: ${stamp} (checkout_head_label=${code_sha}, source_basis=working_tree, working_tree_state=${working_tree_state})"
}

# Written into the container *before* restart; must survive stop/start.
# If compose recreate wiped the docker-cp layer, this nonce is gone.
_SYNC_NONCE=""

write_sync_nonce() {
  local c=mcp-server
  _SYNC_NONCE="$(date -u +%Y%m%dT%H%M%SZ)-$$-$RANDOM"
  docker exec -u 0 "$c" sh -c \
    "printf '%s\n' '${_SYNC_NONCE}' > /app/.source_sync_nonce && chown mcp:mcp /app/.source_sync_nonce"
}

verify_sync_nonce_survived() {
  local c=mcp-server
  local observed
  observed="$(docker exec "$c" cat /app/.source_sync_nonce 2>/dev/null || true)"
  if [[ "${observed}" != "${_SYNC_NONCE}" ]]; then
    echo "ERROR: sync nonce missing after restart (observed='${observed}' expected='${_SYNC_NONCE}')." >&2
    echo "ERROR: container was likely recreated — docker cp layer was wiped. Fix restart path; do not treat this sync as live." >&2
    return 1
  fi
  echo "Verified sync nonce survived restart (writable layer intact)."
}

sync_source_into_container() {
  local c=mcp-server
  echo "Syncing MCP source into ${c} (docker cp — no image rebuild)..."
  docker cp "$WORKSPACE_ROOT/libs/." "${c}:/app/libs/"
  docker cp "$WORKSPACE_ROOT/services/." "${c}:/app/services/"
  docker cp "$WORKSPACE_ROOT/config/." "${c}:/app/config/"
  docker cp "$WORKSPACE_ROOT/sitecustomize.py" "${c}:/app/sitecustomize.py"
  docker cp "$WORKSPACE_ROOT/pipelines/." "${c}:/app/pipelines/"
  if [[ -d "$WORKSPACE_ROOT/pipelines.local" ]]; then
    docker cp "$WORKSPACE_ROOT/pipelines.local/." "${c}:/app/pipelines.local/"
  fi
  if [[ -d "$WORKSPACE_ROOT/services/mcp-server/tools/local" ]]; then
    docker cp "$WORKSPACE_ROOT/services/mcp-server/tools/local/." \
      "${c}:/app/services/mcp-server/tools/local/"
  fi
  docker exec -u 0 "$c" chown -R mcp:mcp \
    /app/libs /app/services /app/config /app/pipelines /app/sitecustomize.py \
    /app/pipelines.local /app/services/mcp-server/tools/local 2>/dev/null || \
  docker exec -u 0 "$c" chown -R mcp:mcp \
    /app/libs /app/services /app/config /app/pipelines /app/sitecustomize.py
  write_sync_nonce
}

ensure_mcp_container() {
  if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx 'mcp-server'; then
    return 0
  fi
  echo "MCP container not found — creating from existing image (no rebuild)..."
  docker compose "${COMPOSE_ARGS[@]}" up -d mcp-server
  wait_for_mcp_healthy || return 1
}

restart_mcp_gracefully() {
  # Must be docker stop/start on the *existing* container — never
  # `compose up -d`. Compose recreate-on-config/image-change replaces the
  # container and drops the writable-layer docker cp (dogfood 6538: sync
  # stamped ok, /app still pre-followup until targeted cp + docker restart).
  local c=mcp-server
  if ! docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$c"; then
    echo "ERROR: ${c} missing after sync — refusing compose recreate (would wipe docker cp)." >&2
    return 1
  fi
  echo "Restarting ${c} (docker stop/start — preserving synced /app layer)..."
  docker stop -t 30 "$c" >/dev/null
  docker start "$c" >/dev/null
}

if [[ "$NO_CACHE" == "true" ]]; then
  echo "Building MCP server (no cache, pulling fresh base images)..."
  bash docker/scripts/build/build-mcp.sh --no-cache
  remove_mcp_container_for_image_recreate
  echo "Starting MCP server..."
  docker compose "${COMPOSE_ARGS[@]}" up -d mcp-server
else
  ensure_mcp_container
  sync_source_into_container
  restart_mcp_gracefully
  verify_sync_nonce_survived
fi

# Stamp must run after the container is up; never gate on health (fleet can pass
# sync while health is still warming). Fail-soft so a stamp error does not mask
# a successful sync.
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'mcp-server'; then
  write_source_sync_stamp || echo "WARNING: failed to write /app/.source_sync_stamp" >&2
fi

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
  echo "WARNING: MCP server not healthy after sync/restart (check docker ps / logs)." >&2
  echo "WARNING: Skipping descriptor refresh because MCP server is not healthy." >&2
fi

echo "Done. Check: docker ps --filter name=mcp-server"
