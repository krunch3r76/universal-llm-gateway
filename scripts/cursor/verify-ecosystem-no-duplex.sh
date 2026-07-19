#!/usr/bin/env bash
# Fail if ecosystem shared skills/rules/commands are also present on Cursor
# discovery paths that duplex the ulg-ecosystem plugin.
#
# Invoked by install-ecosystem-plugin.sh. Exit 1 on any duplex hit.
set -euo pipefail

ULG_ROOT="${1:?usage: verify-ecosystem-no-duplex.sh <ulg-root>}"
PLUGIN_SRC="${ULG_ROOT}/cursor-plugins/ulg-ecosystem"
PARENT_RULES="$(cd "${ULG_ROOT}/.." && pwd)/.cursor/rules"
PARENT_CMDS="$(cd "${ULG_ROOT}/.." && pwd)/.cursor/commands"
HUB_SKILLS_CURSOR="${ULG_ROOT}/.cursor/skills"
HUB_SKILLS_CLAUDE="${ULG_ROOT}/.claude/skills"
HUB_CMDS="${ULG_ROOT}/.cursor/commands"

hits=0
hit() {
  echo "DUPLEX: $*" >&2
  hits=$((hits + 1))
}

# Skills census must not exist under hub .cursor/skills (Cursor discovery)
# OR under hub .claude/skills (shared_sync Customize staging is out-of-tree at
# ~/.gateway/claude-ai-sync/skills/ — in-repo .claude census = duplex regression).
# life_local SOT under .claude/skills/ is allowed and is NOT in SKILLS_CENSUS.
if [[ -f "${PLUGIN_SRC}/SKILLS_CENSUS.txt" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    slug="${line%%#*}"
    slug="$(echo "$slug" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [[ -z "$slug" ]] && continue
    [[ -d "${HUB_SKILLS_CURSOR}/${slug}" ]] && hit "skill ${slug} under .cursor/skills/ (plugin owns census)"
    [[ -d "${HUB_SKILLS_CLAUDE}/${slug}" ]] && hit "skill ${slug} under .claude/skills/ (shared_sync staging is out-of-tree; plugin owns census)"
  done < "${PLUGIN_SRC}/SKILLS_CENSUS.txt"
fi

# _ulg rules must not live under parent inherit path
if [[ -f "${PLUGIN_SRC}/RULES_ULG_CENSUS.txt" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    name="${line%%#*}"
    name="$(echo "$name" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [[ -z "$name" ]] && continue
    [[ -f "${PARENT_RULES}/${name}.mdc" ]] && hit "rule ${name}.mdc under projects/.cursor/rules/ (plugin owns _ulg)"
  done < "${PLUGIN_SRC}/RULES_ULG_CENSUS.txt"
fi

# Shared commands in plugin SoT must not also exist on parent inherit or hub commands
if [[ -d "${PLUGIN_SRC}/commands" ]]; then
  shopt -s nullglob
  for cmd in "${PLUGIN_SRC}/commands"/*.md; do
    base="$(basename "$cmd")"
    [[ -f "${PARENT_CMDS}/${base}" ]] && hit "command ${base} under projects/.cursor/commands/ (plugin owns shared)"
    [[ -f "${HUB_CMDS}/${base}" ]] && hit "command ${base} under hub .cursor/commands/ (plugin owns shared; hub-only cmds ok if absent from plugin)"
  done
  shopt -u nullglob
fi

if [[ "$hits" -gt 0 ]]; then
  echo "ERROR: ${hits} ecosystem duplex hit(s). Cursor would list the same skill/rule/command twice." >&2
  echo "Fix: keep SoT only under cursor-plugins/ulg-ecosystem/; remove the paths above." >&2
  exit 1
fi

echo "duplex guard: OK (no census/_ulg/shared-command copies on inherit or hub discovery paths)"
