#!/usr/bin/env bash
# Install lane-B seat-write hooks into checkout-local .cursor/ (gitignored).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DEST="$ROOT/.cursor"
mkdir -p "$DEST/hooks"
cp "$ROOT/.cursor/hooks/register-seat-write.sh" "$DEST/hooks/"
cp "$ROOT/.cursor/hooks/seat-write-session-start.sh" "$DEST/hooks/"
cp "$ROOT/.cursor/hooks/seat-write-session-end.sh" "$DEST/hooks/"
python3 - <<'PY' "$DEST/hooks.json"
import json, sys
from pathlib import Path
dest = Path(sys.argv[1])
base = {
    "version": 1,
    "hooks": {
        "sessionStart": [{"command": ".cursor/hooks/seat-write-session-start.sh"}],
        "sessionEnd": [{"command": ".cursor/hooks/seat-write-session-end.sh"}],
        "afterFileEdit": [{"command": ".cursor/hooks/register-seat-write.sh"}],
    },
}
# Merge with existing sessionStart verify if present
existing = {}
if dest.exists():
    existing = json.loads(dest.read_text())
merged = {**existing, **base}
merged["version"] = 1
hooks = existing.get("hooks", {})
for event, entries in base["hooks"].items():
    if event == "sessionStart" and event in hooks:
        merged["hooks"][event] = hooks[event] + entries
    else:
        merged.setdefault("hooks", {})[event] = entries
dest.write_text(json.dumps(merged, indent=2) + "\n")
PY
chmod +x "$DEST/hooks/"*.sh
echo "lane-B hooks installed under $DEST"
