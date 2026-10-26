#!/usr/bin/env bash
# Sync MCP Python source into the running container and restart (no image rebuild).
# Pass --no-cache only for pip/Dockerfile/base-image changes (full build-mcp.sh rebuild).
# ¬ curl x.ai/cli/install.sh here — that installer symlinks both grok and agent into
#   ~/.grok/bin (and often ~/.local/bin), which can overwrite the Cursor agent binary.
# Reads MCP_PROJECT_DIR, MCP_AUTH_TOKEN, etc. from ~/.gateway/mcp.yaml.
# Routine path: docker cp into /app + docker stop/start (preserves writable layer).
# When a compose environment key that build_mcp_env would pass is missing from the
# container Config.Env key names, recreate (rm + compose up) BEFORE the cp. stop/start
# keeps create-time Config.Env, so a new key never arrives until recreate.
# --no-cache: compose up -d creates the container (its entrypoint can import before any
# stamp exists). This script then writes /app/.source_sync_stamp and docker stop/start
# so the process that stays up seals the new line 2. That extra stop/start is required
# because code_version caches stamp line 2 at import; it adds a second restart on rebuild.
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
OWNER_HOME="$(getent passwd "$(id -u)" | cut -d: -f6)"
if [[ -z "${OWNER_HOME}" || "${OWNER_HOME}" != /* ]]; then
  echo "ERROR: passwd home for uid $(id -u) missing; refusing mcp compose." >&2
  exit 1
fi
export HOME="${OWNER_HOME}"
PYTHON="${PYTHON:-$HOME/.venvs/universal/bin/python}"
GATE=("$PYTHON" -m deploy_identity.mcp_restart_gate --workspace-root "$WORKSPACE_ROOT")

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

CHECKED_IMAGE_ID=""
CHECKED_IMAGE_FILE="${MCP_DATA_DIR}/last-healthy/checked_image_id"
_SOURCE_SYNC_STAMP_SNAPSHOT=""
_STAMP_WRITTEN=0
_TREE_CHANGED=0
_SYNCED_ROOT_LIST=""
_STOP_SUCCEEDED=0
_START_FAILED=0
_RESTORE_HANDLER_RAN=0

gate() {
  "$PYTHON" -m deploy_identity.mcp_restart_gate --workspace-root "$WORKSPACE_ROOT" --mcp-data-dir "$MCP_DATA_DIR" "$@"
}

gate_emit_log() {
  local msg="$1"
  local data_args=()
  if [[ -n "${MCP_DATA_DIR:-}" ]]; then
    data_args=(--mcp-data-dir "$MCP_DATA_DIR")
  fi
  "$PYTHON" -m deploy_identity.mcp_restart_gate \
    --workspace-root "$WORKSPACE_ROOT" \
    "${data_args[@]}" \
    emit-log -- "$msg" 2>/dev/null || true
}

mcp_sync_error() {
  echo "$1" >&2
  gate_emit_log "$1"
}

_restore_window_exit() {
  trap '' PIPE INT TERM HUP
  trap - EXIT
  set +e
  if [[ "$_RESTORE_HANDLER_RAN" == 1 ]]; then
    exit 1
  fi
  _RESTORE_HANDLER_RAN=1
  local img="${CHECKED_IMAGE_ID:-}"
  if [[ -z "$img" && -f "$CHECKED_IMAGE_FILE" ]]; then
    img="$(tr -d '\n' < "$CHECKED_IMAGE_FILE")"
  fi
  # Tree or stamp changed and the process is still up. gate restore refuses
  # healthy, so roll the last-healthy copy set back when it is complete.
  # Success marker only when both the tree rollback and the stamp restore landed.
  if [[ "${_STOP_SUCCEEDED:-0}" != 1 && ( "${_TREE_CHANGED:-0}" == 1 || "${_STAMP_WRITTEN:-0}" == 1 ) ]]; then
    if _container_still_serving; then
      local rolled=0 stamp_ok=0
      if _last_healthy_copy_set_present && _rollback_running_tree_from_last_healthy; then
        rolled=1
      fi
      if [[ "${_STAMP_WRITTEN:-0}" == 1 ]]; then
        if restore_source_sync_stamp_from_snapshot; then
          stamp_ok=1
        fi
      else
        stamp_ok=1
      fi
      if [[ "$rolled" == 1 && "$stamp_ok" == 1 ]] && _rollback_delete_synced_extras; then
        echo "MCP_SYNC_LAST_HEALTHY_TREE_ROLLBACK" >&2
      else
        echo "MCP_SYNC_TREE_STAMP_DISAGREE" >&2
      fi
      _cleanup_stamp_snapshot
      exit 1
    fi
    if [[ "${_STAMP_WRITTEN:-0}" == 1 ]]; then
      restore_source_sync_stamp_from_snapshot || true
    fi
  fi
  # Start failed after stop. Skip gate restore: plan_restore_commands begins with
  # docker rm -f, and the last-healthy snapshot copy does not include
  # /app/.source_sync_stamp, so recreate would drop the synced tree and the stamp.
  if [[ "${_START_FAILED:-0}" == 1 ]]; then
    _cleanup_stamp_snapshot
    exit 1
  fi
  if [[ -n "$img" ]]; then
    if gate restore --firer script --checked-image-id "$img"; then
      echo "MCP_SYNC_GATE_RESTORE" >&2
    else
      echo "MCP_SYNC_TREE_STAMP_DISAGREE" >&2
    fi
  fi
  _cleanup_stamp_snapshot
  exit 1
}

arm_restore_window() {
  trap _restore_window_exit EXIT
}

purge_mcp_compose_orphans() {
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
  if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx 'mcp-server'; then
    echo "Removing existing mcp-server container before image recreate..."
    docker rm -f mcp-server >/dev/null 2>&1 || true
  fi
}

wait_for_mcp_healthy() {
  local timeout_s="${1:-120}"
  gate wait-healthy --timeout "$timeout_s"
}

_cleanup_stamp_snapshot() {
  if [[ -n "${_SOURCE_SYNC_STAMP_SNAPSHOT:-}" ]]; then
    rm -f "${_SOURCE_SYNC_STAMP_SNAPSHOT}"
  fi
}

snapshot_source_sync_stamp() {
  local c=mcp-server
  if ! docker exec "$c" true; then
    mcp_sync_error "ERROR: docker exec failed while snapshotting /app/.source_sync_stamp; not treating the stamp as absent." >&2
    return 1
  fi
  if docker exec "$c" test -f /app/.source_sync_stamp; then
    docker exec "$c" cat /app/.source_sync_stamp > "$_SOURCE_SYNC_STAMP_SNAPSHOT"
  else
    : > "$_SOURCE_SYNC_STAMP_SNAPSHOT"
  fi
}

restore_source_sync_stamp_from_snapshot() {
  local c=mcp-server
  if [[ ! -f "${_SOURCE_SYNC_STAMP_SNAPSHOT:-}" ]]; then
    return 1
  fi
  if [[ ! -s "$_SOURCE_SYNC_STAMP_SNAPSHOT" ]]; then
    docker exec -u 0 "$c" rm -f /app/.source_sync_stamp
    return
  fi
  docker exec -i -u 0 "$c" sh -c 'cat > /app/.source_sync_stamp && chown mcp:mcp /app/.source_sync_stamp' \
    < "$_SOURCE_SYNC_STAMP_SNAPSHOT"
}

_container_still_serving() {
  local status
  status="$(
    docker inspect \
      -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
      mcp-server 2>/dev/null || true
  )"
  case "$status" in
    healthy|restarting|starting|running) return 0 ;;
    *) return 1 ;;
  esac
}

_last_healthy_copy_set_present() {
  local base="${MCP_DATA_DIR}/last-healthy"
  local name
  for name in libs services config pipelines; do
    [[ -d "${base}/${name}" ]] || return 1
  done
  [[ -f "${base}/sitecustomize.py" ]]
}

_rollback_running_tree_from_last_healthy() {
  local base="${MCP_DATA_DIR}/last-healthy"
  local c=mcp-server
  local name
  for name in libs services config pipelines; do
    docker cp "${base}/${name}/." "${c}:/app/${name}/" || return 1
  done
  if [[ -d "${base}/pipelines.local" ]]; then
    docker cp "${base}/pipelines.local/." "${c}:/app/pipelines.local/" || return 1
  fi
  docker cp "${base}/sitecustomize.py" "${c}:/app/sitecustomize.py"
}

_note_synced_root() {
  _SYNCED_ROOT_LIST+="${1}"$'\n'
}

# Relative paths under one synced root, excluding __pycache__ and *.pyc.
_list_root() {
  local base="$1" root="$2" rel
  local dir="${base}/${root}"
  if [[ ! -d "$dir" ]]; then
    return 0
  fi
  find "$dir" \( -name '__pycache__' -o -name '*.pyc' \) -prune -o -printf '%P\n' \
    | while IFS= read -r rel; do
        if [[ -z "$rel" ]]; then
          printf '%s\n' "$root"
        else
          printf '%s\n' "${root}/${rel}"
        fi
      done
}

_collect_candidates() {
  local base="${MCP_DATA_DIR}/last-healthy" root src dst
  while IFS= read -r root; do
    [[ -z "$root" ]] && continue
    if [[ ! -e "${base}/${root}" ]]; then
      printf '%s\n' "$root"
      continue
    fi
    src="$(mktemp)"
    dst="$(mktemp)"
    _list_root "$WORKSPACE_ROOT" "$root" | sort -u > "$src"
    _list_root "$base" "$root" | sort -u > "$dst"
    comm -23 "$src" "$dst"
    rm -f "$src" "$dst"
  done <<< "${_SYNCED_ROOT_LIST}"
}

_candidates_hit_mount() {
  local mounts="$1" cand="$2" rel dest cpath
  while IFS= read -r rel; do
    [[ -z "$rel" ]] && continue
    cpath="/app/${rel}"
    while IFS= read -r dest; do
      [[ -z "$dest" ]] && continue
      dest="${dest%/}"
      # Equal, under the mount, or an ancestor of it. A slash boundary keeps
      # /app/services/new from matching /app/services/newpkg/vol.
      if [[ "$cpath" == "$dest" || "$cpath" == "$dest"/* || "$dest" == "$cpath"/* ]]; then
        return 0
      fi
    done <<< "$mounts"
  done < "$cand"
  return 1
}

_write_delete_stream() {
  local cand="$1" rel files dirs
  files="$(mktemp)"
  dirs="$(mktemp)"
  while IFS= read -r rel; do
    [[ -z "$rel" ]] && continue
    if [[ -d "${WORKSPACE_ROOT}/${rel}" ]]; then
      printf '%s\n' "$rel" >> "$dirs"
    else
      printf '%s\n' "$rel" >> "$files"
    fi
  done < "$cand"
  while IFS= read -r rel; do
    [[ -z "$rel" ]] && continue
    printf 'F /app/%s\n' "$rel"
  done < "$files"
  if [[ -s "$dirs" ]]; then
    awk -F/ '{print NF "\t" $0}' "$dirs" | sort -nr -k1,1 | cut -f2- \
      | while IFS= read -r rel; do
          [[ -z "$rel" ]] && continue
          printf 'D /app/%s\n' "$rel"
        done
  fi
  rm -f "$files" "$dirs"
}

_normalize_container_listing() {
  sed -e 's|^/app/||' -e 's|/$||' -e '/^$/d' | sort -u
}

_listing_matches() {
  local expected actual root absent saw_root=0
  local -a find_args=()
  local -a absent_roots=()
  expected="$(mktemp)"
  actual="$(mktemp)"
  while IFS= read -r root; do
    [[ -z "$root" ]] && continue
    saw_root=1
    if [[ -d "${MCP_DATA_DIR}/last-healthy/${root}" ]]; then
      _list_root "${MCP_DATA_DIR}/last-healthy" "$root" >> "$expected"
      find_args+=("/app/${root}")
    else
      absent_roots+=("$root")
    fi
  done <<< "${_SYNCED_ROOT_LIST}"
  if [[ ${#absent_roots[@]} -gt 0 ]]; then
    for absent in "${absent_roots[@]}"; do
      if docker exec mcp-server test -e "/app/${absent}"; then
        rm -f "$expected" "$actual"
        return 1
      fi
    done
  fi
  # No synced root, or nothing to list, is not a silent match of two empty files.
  if [[ ${#find_args[@]} -eq 0 ]]; then
    rm -f "$expected" "$actual"
    if [[ "$saw_root" -eq 1 ]]; then
      return 0
    fi
    return 1
  fi
  sort -u "$expected" -o "$expected"
  if ! docker exec mcp-server find "${find_args[@]}" \
      \( -name '__pycache__' -o -name '*.pyc' \) -prune -o -print \
      | _normalize_container_listing > "$actual"; then
    rm -f "$expected" "$actual"
    return 1
  fi
  local rc=0
  cmp -s "$expected" "$actual" || rc=1
  rm -f "$expected" "$actual"
  return "$rc"
}

# Delete paths this sync copied that last-healthy lacks. Return 0 only when
# the read-only container listing of the synced roots then matches last-healthy.
_rollback_delete_synced_extras() {
  local mounts cand stream flat
  if ! mounts="$(
    docker inspect -f '{{range .Mounts}}{{.Destination}}{{"\n"}}{{end}}' mcp-server
  )"; then
    return 1
  fi
  cand="$(mktemp)"
  stream="$(mktemp)"
  _collect_candidates > "$cand"
  flat="${mounts//[[:space:]]/}"
  if [[ -n "$flat" ]] && _candidates_hit_mount "$mounts" "$cand"; then
    rm -f "$cand" "$stream"
    return 1
  fi
  if [[ -s "$cand" ]]; then
    _write_delete_stream "$cand" > "$stream"
    if ! docker exec -i -u 0 mcp-server sh -c 'while IFS= read -r line; do kind="${line%% *}"; path="${line#* }"; if [[ "$kind" == F ]]; then rm -f -- "$path" || exit 1; else rm -rf -- "$path" || exit 1; fi; done' < "$stream"; then
      rm -f "$cand" "$stream"
      return 1
    fi
  fi
  rm -f "$cand" "$stream"
  _listing_matches
}

write_source_sync_stamp_before_serving_restart() {
  arm_restore_window
  _SOURCE_SYNC_STAMP_SNAPSHOT="$(mktemp)"
  snapshot_source_sync_stamp
  _STAMP_WRITTEN=1
  write_source_sync_stamp
}

read_stamp_classification() {
  "$PYTHON" -c '
import sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path[:0] = [str(root / "libs"), str(root / "scripts/model_manager/ui/controller")]
from fleet_liveness_probe import classify_stamp_worktree
fields = classify_stamp_worktree(root)
for key in (
    "working_tree_state",
    "load_surface_worktree_state",
    "load_surface_classification",
):
    print(f"{key}={fields[key]}")
' "$1"
}

write_source_sync_stamp() {
  local c=mcp-server
  local stamp code_sha working_tree_state source_tree_sha image_id
  local load_surface_worktree_state load_surface_classification classification
  stamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  image_id="$(docker inspect -f '{{.Image}}' mcp-server 2>/dev/null || true)"
  code_sha="$(git -C "$WORKSPACE_ROOT" rev-parse HEAD)"
  source_tree_sha="$(
    find "$WORKSPACE_ROOT/libs" "$WORKSPACE_ROOT/services/mcp-server" -type f \
      ! -path '*/__pycache__/*' ! -name '*.pyc' -print0 \
      | sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}'
  )"
  classification="$(read_stamp_classification "$WORKSPACE_ROOT" 2>/dev/null)" || classification=""
  working_tree_state="$(printf '%s\n' "$classification" | sed -n 's/^working_tree_state=//p')"
  load_surface_worktree_state="$(printf '%s\n' "$classification" | sed -n 's/^load_surface_worktree_state=//p')"
  load_surface_classification="$(printf '%s\n' "$classification" | sed -n 's/^load_surface_classification=//p')"
  if [[ -n "${_STAMP_CLASS_BEFORE+x}" ]]; then
    if [[ "$classification" != "${_STAMP_CLASS_BEFORE}" || "$code_sha" != "${_STAMP_HEAD_BEFORE}" ]]; then
      working_tree_state="unknown"
      load_surface_worktree_state="unknown"
      load_surface_classification="unknown"
    fi
  fi
  case "$working_tree_state" in
    clean|dirty|unknown) ;;
    *) working_tree_state="unknown" ;;
  esac
  case "$load_surface_worktree_state" in
    clean|dirty|unknown) ;;
    *) load_surface_worktree_state="unknown" ;;
  esac
  case "$load_surface_classification" in
    none|untracked_off_load_surface_only|off_load_surface_only|load_surface_dirty|unknown) ;;
    *) load_surface_classification="unknown" ;;
  esac
  docker exec -u 0 "$c" sh -c \
    "printf '%s\n%s\nsource_basis=working_tree\ncode_version_semantics=checkout_head_at_source_sync\nworking_tree_state=${working_tree_state}\nload_surface_worktree_state=${load_surface_worktree_state}\nload_surface_classification=${load_surface_classification}\nsource_tree_sha=${source_tree_sha}\nimage_id=${image_id}\n' '${stamp}' '${code_sha}' > /app/.source_sync_stamp && chown mcp:mcp /app/.source_sync_stamp"
  echo "Wrote source sync stamp: ${stamp} (checkout_head_label=${code_sha}, source_basis=working_tree, working_tree_state=${working_tree_state}, load_surface_worktree_state=${load_surface_worktree_state}, load_surface_classification=${load_surface_classification})"
}

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
    mcp_sync_error "ERROR: sync nonce missing after restart (observed='${observed}' expected='${_SYNC_NONCE}')." >&2
    mcp_sync_error "ERROR: container was likely recreated — docker cp layer was wiped. Fix restart path; do not treat this sync as live." >&2
    return 1
  fi
  echo "Verified sync nonce survived restart (writable layer intact)."
}

_mcp_container_running() {
  docker inspect -f '{{.State.Running}}' mcp-server 2>/dev/null | grep -qx true
}

_chown_mcp_app_tree() {
  local c=mcp-server img
  img="$(docker inspect -f '{{.Config.Image}}' "$c" 2>/dev/null || true)"
  if [[ -z "$img" ]]; then
    mcp_sync_error "ERROR: cannot resolve image for ${c} chown." >&2
    return 1
  fi
  if _mcp_container_running; then
    docker exec -u 0 "$c" chown -R mcp:mcp \
      /app/libs /app/services /app/config /app/pipelines /app/sitecustomize.py \
      /app/pipelines.local /app/services/mcp-server/tools/local 2>/dev/null || \
    docker exec -u 0 "$c" chown -R mcp:mcp \
      /app/libs /app/services /app/config /app/pipelines /app/sitecustomize.py
  else
    docker run --rm --volumes-from "$c" --user 0 "$img" chown -R mcp:mcp \
      /app/libs /app/services /app/config /app/pipelines /app/sitecustomize.py \
      /app/pipelines.local /app/services/mcp-server/tools/local 2>/dev/null || \
    docker run --rm --volumes-from "$c" --user 0 "$img" chown -R mcp:mcp \
      /app/libs /app/services /app/config /app/pipelines /app/sitecustomize.py
  fi
}

sync_source_into_container() {
  local c=mcp-server
  arm_restore_window
  _TREE_CHANGED=1
  echo "Syncing MCP source into ${c} (docker cp — no image rebuild)..."
  _note_synced_root libs
  docker cp "$WORKSPACE_ROOT/libs/." "${c}:/app/libs/"
  _note_synced_root services
  docker cp "$WORKSPACE_ROOT/services/." "${c}:/app/services/"
  _note_synced_root config
  docker cp "$WORKSPACE_ROOT/config/." "${c}:/app/config/"
  docker cp "$WORKSPACE_ROOT/sitecustomize.py" "${c}:/app/sitecustomize.py"
  _note_synced_root pipelines
  docker cp "$WORKSPACE_ROOT/pipelines/." "${c}:/app/pipelines/"
  if [[ -d "$WORKSPACE_ROOT/pipelines.local" ]]; then
    _note_synced_root pipelines.local
    docker cp "$WORKSPACE_ROOT/pipelines.local/." "${c}:/app/pipelines.local/"
  fi
  if [[ -d "$WORKSPACE_ROOT/services/mcp-server/tools/local" ]]; then
    _note_synced_root "services/mcp-server/tools/local"
    docker cp "$WORKSPACE_ROOT/services/mcp-server/tools/local/." \
      "${c}:/app/services/mcp-server/tools/local/"
  fi
  _chown_mcp_app_tree
  if _mcp_container_running; then
    write_sync_nonce
  fi
}

# Create container stopped, sync working tree, then start — never boot stale image COPY alone.
bootstrap_mcp_container_from_tree() {
  echo "Creating mcp-server (stopped) → sync tree → start..."
  arm_restore_window
  docker compose "${COMPOSE_ARGS[@]}" up -d --no-start mcp-server
  sync_source_into_container
  docker start mcp-server
}

ensure_mcp_container() {
  if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx 'mcp-server'; then
    return 0
  fi
  bootstrap_mcp_container_from_tree
  wait_for_mcp_healthy || return 1
}

# Return 0 when passthrough key names are missing (caller recreates). Return 1 when
# the container already has every such name (caller keeps stop/start). Exit on inspect
# or compare failure — stop/start would keep a stale Config.Env.
container_passthrough_env_keys_differ() {
  local rc
  # Names only. split/index runs inside docker so values are not on stdout, stderr, or disk.
  set +e
  docker inspect -f '{{range .Config.Env}}{{println (index (split . "=") 0)}}{{end}}' mcp-server \
    | "$PYTHON" -m deploy_identity.mcp_env_keys \
        --workspace-root "$WORKSPACE_ROOT" \
        --compose-browser "$ENABLE_BROWSER_TOOLS" \
        --inspect-file -
  rc=$?
  set -e
  case "$rc" in
    0) return 1 ;;
    10) return 0 ;;
    *)
      mcp_sync_error "ERROR: MCP env key-name compare failed (exit ${rc})." >&2
      exit 1
      ;;
  esac
}

restart_mcp_gracefully() {
  local c=mcp-server
  if ! docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$c"; then
    mcp_sync_error "ERROR: ${c} missing after sync — refusing compose recreate (would wipe docker cp)." >&2
    return 1
  fi
  echo "Restarting ${c} (docker stop/start — preserving synced /app layer)..."
  arm_restore_window
  if ! docker stop -t 30 "$c" >/dev/null; then
    return 1
  fi
  _STOP_SUCCEEDED=1
  if ! docker start "$c" >/dev/null; then
    _START_FAILED=1
    return 1
  fi
}

record_checked_image_if_healthy() {
  local status
  status="$(
    docker inspect \
      -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
      mcp-server 2>/dev/null || true
  )"
  if [[ "$status" != "healthy" ]]; then
    return 0
  fi
  CHECKED_IMAGE_ID="$(docker inspect -f '{{.Image}}' mcp-server 2>/dev/null || true)"
  if [[ -n "$CHECKED_IMAGE_ID" ]]; then
    mkdir -p "$(dirname "$CHECKED_IMAGE_FILE")"
    printf '%s\n' "$CHECKED_IMAGE_ID" > "$CHECKED_IMAGE_FILE"
  fi
  gate snapshot-if-healthy || true
}

run_import_probe_or_refuse() {
  local rc=0
  set +e
  gate import-probe
  rc=$?
  set -e
  if [[ "$rc" == 2 ]]; then
    mcp_sync_error "ERROR: MCP import probe timed out (120s wall cap)." >&2
    exit 1
  fi
  if [[ "$rc" != 0 ]]; then
    mcp_sync_error "ERROR: MCP import probe failed — refusing restart (no cp/stop/rm)." >&2
    exit 1
  fi
}

mkdir -p "$MCP_DATA_DIR"
exec 9>"${MCP_DATA_DIR}/sync-and-restart-mcp.lock"
if ! flock -n 9; then
  mcp_sync_error "ERROR: another sync-and-restart-mcp.sh is already running." >&2
  exit 1
fi

# Pin snapshot and import probe before ensure_mcp_container on the routine path.
record_checked_image_if_healthy

if [[ "$NO_CACHE" != "true" ]]; then
  run_import_probe_or_refuse
fi

if [[ "$NO_CACHE" == "true" ]]; then
  nocache_status="$(
    docker inspect \
      -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
      mcp-server 2>/dev/null || true
  )"
  if [[ "$nocache_status" != "healthy" ]]; then
    mcp_sync_error "ERROR: --no-cache refused — mcp-server status is '${nocache_status:-missing}', not healthy. Not starting the build." >&2
    exit 1
  fi
  record_checked_image_if_healthy
  gate tag-last-healthy || true
  echo "Building MCP server (no cache, pulling fresh base images)..."
  bash docker/scripts/build/build-mcp.sh --no-cache
  set +e
  gate image-boot-probe
  boot_probe_rc=$?
  gate import-probe
  probe_rc=$?
  set -e
  if [[ "$boot_probe_rc" != 0 || "$probe_rc" != 0 ]]; then
    gate retag-local-from-last-healthy || true
    mcp_sync_error "ERROR: post-build MCP probe failed (image-boot=${boot_probe_rc}, import=${probe_rc}) — reverted :local to :last-healthy." >&2
    exit 1
  fi
  INCOMING_SHA="$(gate source-tree-sha)"
  gate write-pending "$INCOMING_SHA"
  arm_restore_window
  remove_mcp_container_for_image_recreate
  echo "Starting MCP server (bootstrap sync before first boot)..."
  bootstrap_mcp_container_from_tree
  write_source_sync_stamp_before_serving_restart
  restart_mcp_gracefully
else
  ensure_mcp_container
  if container_passthrough_env_keys_differ; then
    echo "MCP container env key names differ; recreating before source sync."
    INCOMING_SHA="$(gate source-tree-sha)"
    gate write-pending "$INCOMING_SHA"
    arm_restore_window
    remove_mcp_container_for_image_recreate
    if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx 'mcp-server'; then
      mcp_sync_error "ERROR: mcp-server still present after rm; refusing compose up." >&2
      exit 1
    fi
    arm_restore_window
    bootstrap_mcp_container_from_tree
  fi
  _STAMP_HEAD_BEFORE="$(git -C "$WORKSPACE_ROOT" rev-parse HEAD)"
  _STAMP_CLASS_BEFORE="$(read_stamp_classification "$WORKSPACE_ROOT" 2>/dev/null || true)"
  sync_source_into_container
  INCOMING_SHA="$(gate source-tree-sha)"
  gate write-pending "$INCOMING_SHA"
  write_source_sync_stamp_before_serving_restart
  restart_mcp_gracefully
fi

trap _cleanup_stamp_snapshot EXIT

if wait_for_mcp_healthy; then
  verify_sync_nonce_survived
  gate promote-booted
  _emit_sync_args=(emit-direct-sync-success)
  if [[ "$NO_CACHE" == "true" ]]; then
    _emit_sync_args+=(--no-cache)
  fi
  if [[ -n "${CHECKED_IMAGE_ID:-}" ]]; then
    _emit_sync_args+=(--image-id "$CHECKED_IMAGE_ID")
  fi
  gate "${_emit_sync_args[@]}" || true
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
  img="${CHECKED_IMAGE_ID:-}"
  if [[ -z "$img" && -f "$CHECKED_IMAGE_FILE" ]]; then
    img="$(tr -d '\n' < "$CHECKED_IMAGE_FILE")"
  fi
  if [[ -n "$img" ]]; then
    if gate restore --firer script --checked-image-id "$img"; then
      echo "MCP_SYNC_GATE_RESTORE" >&2
    else
      echo "MCP_SYNC_TREE_STAMP_DISAGREE" >&2
    fi
    wait_for_mcp_healthy || exit 1
  else
    exit 1
  fi
fi

echo "Done. Check: docker ps --filter name=mcp-server"
