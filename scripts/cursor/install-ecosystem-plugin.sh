#!/usr/bin/env bash
# Assemble ulg-ecosystem plugin from repo SoT and install to
# ~/.cursor/plugins/local/ulg-ecosystem/ (Cursor local-plugin path).
#
# Usage:
#   ./scripts/cursor/install-ecosystem-plugin.sh
#   ./scripts/cursor/install-ecosystem-plugin.sh --dry-run
#
# After install: Developer → Reload Window (or restart Cursor).

set -euo pipefail

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ULG_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Fail closed when HOME is a cursor-sdk dispatch overlay (option b — arc 6655).
# Guard uses passwd-home python so a contaminated HOME cannot disable detection.
_passwd_home="$(getent passwd "${USER:-$(id -un)}" | cut -d: -f6)"
_guard_py="${_passwd_home}/.venvs/universal/bin/python3"
if [[ ! -x "${_guard_py}" ]]; then
  _guard_py="python3"
fi
if ! "${_guard_py}" -m services.git_integration_worker.dispatch_home_host_guard \
    "bash ${SCRIPT_DIR}/install-ecosystem-plugin.sh"; then
  exit 1
fi

PLUGIN_SRC="${ULG_ROOT}/cursor-plugins/ulg-ecosystem"
CENSUS="${PLUGIN_SRC}/SKILLS_CENSUS.txt"
RULES_CENSUS="${PLUGIN_SRC}/RULES_ULG_CENSUS.txt"
# Shared commands + _ulg rules + census skills: plugin tree is sole Cursor SoT.
COMMANDS_SOT="${PLUGIN_SRC}/commands"
RULES_SOT="${PLUGIN_SRC}/rules"
INSTALL_DIR="${HOME}/.cursor/plugins/local/ulg-ecosystem"
STAGING="${TMPDIR:-/tmp}/ulg-ecosystem-plugin-staging-$$"

cleanup() {
  rm -rf "$STAGING"
}
trap cleanup EXIT

die() { echo "ERROR: $*" >&2; exit 1; }

[[ -d "$PLUGIN_SRC" ]] || die "plugin SoT missing: $PLUGIN_SRC"
[[ -f "$CENSUS" ]] || die "census missing: $CENSUS"
[[ -d "$COMMANDS_SOT" ]] || die "commands SoT missing: $COMMANDS_SOT"

# Block install if shared material was re-added onto inherit/hub discovery paths.
VERIFY_DUPLEX="${SCRIPT_DIR}/verify-ecosystem-no-duplex.sh"
[[ -x "$VERIFY_DUPLEX" ]] || chmod +x "$VERIFY_DUPLEX"
"$VERIFY_DUPLEX" "$ULG_ROOT"

PYTHON="${HOME}/.venvs/universal/bin/python3"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="python3"
fi
SOURCE_REPO="${GIT_INTEGRATION_SOURCE_REPO:-/mnt/torus/projects/universal-llm-gateway}"
SOURCE_REPO="$(cd "$SOURCE_REPO" 2>/dev/null && pwd || echo "$SOURCE_REPO")"
# SOT parity must run against the live source checkout: gitignored life_local and
# .cursor/skills bodies exist on that disk, and validate_skill_catalog must load
# catalog.py from the same tree (worktree libs pin _REPO_ROOT to the arc checkout).
VALIDATE_SCRIPT="$SOURCE_REPO/scripts/cortex/validate_skill_catalog.py"
[[ -f "$VALIDATE_SCRIPT" ]] || VALIDATE_SCRIPT="$ULG_ROOT/scripts/cortex/validate_skill_catalog.py"
if ! "$PYTHON" "$VALIDATE_SCRIPT" --root "$SOURCE_REPO"; then
  die "census↔catalog parity failed — add matching config/skills.yaml row before install"
fi
# alwaysApply token budget must run against the live source checkout: the census
# derives plugin/hub/parent scan trees from Path(__file__).parents[2], so invoking
# a worktree copy would miss the live parent pack. Fail closed at the G0 10K hard
# ceiling so a later rule edit cannot silently regress the always-applied plane.
CENSUS_SCRIPT="$SOURCE_REPO/scripts/cursor/alwaysapply_rules_census.py"
[[ -f "$CENSUS_SCRIPT" ]] || CENSUS_SCRIPT="$ULG_ROOT/scripts/cursor/alwaysapply_rules_census.py"
if ! "$PYTHON" "$CENSUS_SCRIPT" --quiet --check 10000; then
  die "alwaysApply token budget exceeded — thin kernels before install (10K hard ceiling)"
fi

resolve_plugin_asset() {
  local rel="$1"
  if [[ -e "$PLUGIN_SRC/$rel" ]]; then
    printf '%s\n' "$PLUGIN_SRC/$rel"
  elif [[ -e "$SOURCE_REPO/cursor-plugins/ulg-ecosystem/$rel" ]]; then
    printf '%s\n' "$SOURCE_REPO/cursor-plugins/ulg-ecosystem/$rel"
  else
    return 1
  fi
}

echo "==> Assembling ulg-ecosystem from $PLUGIN_SRC"
rm -rf "$STAGING"
mkdir -p "$STAGING"/{.cursor-plugin,skills,commands,rules,hooks,scripts}

# Static assets (plugin.json is gitignored — arc worktrees fall back to source repo)
PLUGIN_JSON="$(resolve_plugin_asset ".cursor-plugin/plugin.json")" \
  || die "plugin manifest missing in worktree and source repo"
cp -a "$PLUGIN_JSON" "$STAGING/.cursor-plugin/"
HOOKS_JSON="$(resolve_plugin_asset "hooks/hooks.json")" \
  || die "hooks.json missing in worktree and source repo"
cp -a "$HOOKS_JSON" "$STAGING/hooks/"
VERIFY_LIBS="$(resolve_plugin_asset "scripts/verify-ulg-libs.sh")" \
  || die "verify-ulg-libs.sh missing in worktree and source repo"
cp -a "$VERIFY_LIBS" "$STAGING/scripts/"
chmod +x "$STAGING/scripts/verify-ulg-libs.sh"
cp -a "$PLUGIN_SRC/README.md" "$STAGING/"
cp -a "$CENSUS" "$STAGING/SKILLS_CENSUS.txt"
[[ -f "$RULES_CENSUS" ]] && cp -a "$RULES_CENSUS" "$STAGING/RULES_ULG_CENSUS.txt"
[[ -f "$PLUGIN_SRC/RULES_ULG_CENSUS.md" ]] && cp -a "$PLUGIN_SRC/RULES_ULG_CENSUS.md" "$STAGING/"
[[ -f "$PLUGIN_SRC/SATELLITES.txt" ]] && cp -a "$PLUGIN_SRC/SATELLITES.txt" "$STAGING/"
if [[ -d "$PLUGIN_SRC/templates" ]]; then
  mkdir -p "$STAGING/templates"
  cp -a "$PLUGIN_SRC/templates/." "$STAGING/templates/"
fi

# Skills from census
MISSING=0
while IFS= read -r line || [[ -n "$line" ]]; do
  # trim
  slug="${line%%#*}"
  slug="$(echo "$slug" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  [[ -z "$slug" ]] && continue

  dest="$STAGING/skills/$slug"
  mkdir -p "$dest"

  # SoT = plugin tree (sole Cursor discovery). Fallback: hub .cursor only.
  # ¬ .claude/skills — shared_sync staging is out-of-tree; census there is duplex.
  src=""
  if [[ -f "$PLUGIN_SRC/skills/$slug/SKILL.md" ]]; then
    src="$PLUGIN_SRC/skills/$slug/SKILL.md"
  elif [[ -f "$ULG_ROOT/.cursor/skills/$slug/SKILL.md" ]]; then
    src="$ULG_ROOT/.cursor/skills/$slug/SKILL.md"
  else
    echo "  MISSING skill body: $slug" >&2
    MISSING=$((MISSING + 1))
    continue
  fi

  cp -a "$src" "$dest/SKILL.md"
  # Copy companion files if present next to SoT
  src_dir="$(dirname "$src")"
  shopt -s nullglob
  for extra in "$src_dir"/*; do
    base="$(basename "$extra")"
    [[ "$base" == "SKILL.md" ]] && continue
    [[ -f "$extra" ]] && cp -a "$extra" "$dest/"
  done
  shopt -u nullglob
  echo "  skill: $slug ← $src"
done < "$CENSUS"

[[ "$MISSING" -eq 0 ]] || die "$MISSING skill(s) missing from census"

# Commands from plugin SoT (sole Cursor discovery for shared commands)
cmd_count=0
shopt -s nullglob
for cmd in "$COMMANDS_SOT"/*.md; do
  cp -a "$cmd" "$STAGING/commands/"
  cmd_count=$((cmd_count + 1))
done
shopt -u nullglob
[[ "$cmd_count" -gt 0 ]] || die "no commands found in $COMMANDS_SOT"
echo "  commands: $cmd_count from $COMMANDS_SOT"

# Rules from _ulg census (SoT: cursor-plugins/ulg-ecosystem/rules/)
rule_count=0
rule_missing=0
if [[ -f "$RULES_CENSUS" ]]; then
  [[ -d "$RULES_SOT" ]] || die "rules SoT missing: $RULES_SOT"
  while IFS= read -r line || [[ -n "$line" ]]; do
    name="${line%%#*}"
    name="$(echo "$name" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [[ -z "$name" ]] && continue
    src="$RULES_SOT/${name}.mdc"
    if [[ ! -f "$src" ]]; then
      echo "  MISSING rule: $src" >&2
      rule_missing=$((rule_missing + 1))
      continue
    fi
    cp -a "$src" "$STAGING/rules/"
    rule_count=$((rule_count + 1))
    echo "  rule: ${name}.mdc ← $src"
  done < "$RULES_CENSUS"
  [[ "$rule_missing" -eq 0 ]] || die "$rule_missing _ulg rule(s) missing"
  echo "  rules: $rule_count from $RULES_SOT"
else
  echo "  rules: SKIP (no RULES_ULG_CENSUS.txt)"
fi

# Also refresh assembled skills/commands/rules into plugin SoT tree (repo checkout).
# NEVER delete PLUGIN_SRC root — only replace leaf content under known subdirs.
if [[ "$DRY_RUN" -eq 0 ]]; then
  echo "==> Refreshing plugin SoT skills/ + commands/ + rules/ under $PLUGIN_SRC"
  mkdir -p "$PLUGIN_SRC/skills/satellite-workspace" "$PLUGIN_SRC/commands" "$PLUGIN_SRC/rules"

  # Replace skill dirs except keep satellite-workspace SoT if staging lacks updates from .claude
  for d in "$STAGING/skills"/*; do
    [[ -d "$d" ]] || continue
    slug="$(basename "$d")"
    rm -rf "$PLUGIN_SRC/skills/$slug"
    cp -a "$d" "$PLUGIN_SRC/skills/$slug"
  done

  # Commands: replace contents
  find "$PLUGIN_SRC/commands" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
  cp -a "$STAGING/commands/." "$PLUGIN_SRC/commands/"

  # Rules: replace contents
  find "$PLUGIN_SRC/rules" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
  if [[ "$rule_count" -gt 0 ]]; then
    cp -a "$STAGING/rules/." "$PLUGIN_SRC/rules/"
  fi
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "==> DRY RUN — staging at $STAGING (not installing)"
  echo "    skills: $(find "$STAGING/skills" -name SKILL.md | wc -l)"
  echo "    commands: $(ls "$STAGING/commands" | wc -l)"
  echo "    rules: $(ls "$STAGING/rules" 2>/dev/null | wc -l)"
  exit 0
fi

echo "==> Installing to $INSTALL_DIR"
mkdir -p "$(dirname "$INSTALL_DIR")"
# Replace install dir atomically via temp rename
INSTALL_TMP="${INSTALL_DIR}.new-$$"
rm -rf "$INSTALL_TMP"
mkdir -p "$INSTALL_TMP"
cp -a "$STAGING"/. "$INSTALL_TMP/"
rm -rf "$INSTALL_DIR"
mv "$INSTALL_TMP" "$INSTALL_DIR"

# Sync universal-venv pin into every listed satellite (continual drift correction).
# SoT: templates/satellite-vscode-settings.json + templates/satellite.envrc
# Roster: SATELLITES.txt — add/remove names there; re-run this script.
sync_satellite_pins() {
  local roster="$PLUGIN_SRC/SATELLITES.txt"
  local tpl_vscode="$PLUGIN_SRC/templates/satellite-vscode-settings.json"
  local tpl_envrc="$PLUGIN_SRC/templates/satellite.envrc"
  local projects_root
  projects_root="$(cd "$ULG_ROOT/.." && pwd)"
  [[ -f "$roster" ]] || { echo "  pins: SKIP (no SATELLITES.txt)"; return 0; }
  [[ -f "$tpl_vscode" && -f "$tpl_envrc" ]] || die "pin templates missing under $PLUGIN_SRC/templates/"

  local pinned=0 skipped=0
  while IFS= read -r line || [[ -n "$line" ]]; do
    name="${line%%#*}"
    name="$(echo "$name" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [[ -z "$name" ]] && continue
    [[ "$name" == "universal-llm-gateway" ]] && continue
    sat="$projects_root/$name"
    if [[ ! -d "$sat" ]]; then
      echo "  pin SKIP (missing dir): $name"
      skipped=$((skipped + 1))
      continue
    fi
    if [[ -f "$sat/.cursor/mcp.json" ]]; then
      die "satellite $name has .cursor/mcp.json — shadows user-global vortex-code/life; delete it"
    fi
    mkdir -p "$sat/.vscode"
    cp -a "$tpl_vscode" "$sat/.vscode/settings.json"
    cp -a "$tpl_envrc" "$sat/.envrc"
    echo "  pin: $name → universal venv"
    pinned=$((pinned + 1))
  done < "$roster"
  echo "  pins: $pinned synced, $skipped skipped"
}

echo "==> Syncing satellite universal-venv pins from SATELLITES.txt"
sync_satellite_pins

echo
echo "Installed ulg-ecosystem → $INSTALL_DIR"
echo "  skills:   $(find "$INSTALL_DIR/skills" -name SKILL.md | wc -l)"
echo "  commands: $(ls "$INSTALL_DIR/commands" | wc -l)"
echo "  rules:    $(ls "$INSTALL_DIR/rules" 2>/dev/null | wc -l)"
echo "  manifest: $INSTALL_DIR/.cursor-plugin/plugin.json"
echo
echo "DONE: plugin install is cursor-seat duty (¬ ask operator to sync/install)."
echo "NOTE: Developer → Reload Window only if IDE skill/rule picker still stale after install."
echo "THEN: Settings → Plugins → confirm 'ulg-ecosystem' is Installed"
echo "Venv: all satellites in SATELLITES.txt pinned to \$HOME/.venvs/universal (re-run install to correct drift)"
