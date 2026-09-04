#!/usr/bin/env bash
# Install Jupiter CDP user units (run on Jupiter only; G5 does not invoke until F1 PASS).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
USER_SYSTEMD="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
LOCAL_BIN="$HOME/.local/bin"
SRC="$REPO/services/jupiter-cdp"

mkdir -p "$USER_SYSTEMD" "$LOCAL_BIN" "$USER_SYSTEMD/jupiter-cdp.target.d"

cat >"$USER_SYSTEMD/jupiter-cdp.target.d/repo.conf" <<EOF
[Service]
Environment=ULG_REPO=$REPO
Environment=PYTHONPATH=$REPO/libs
EOF

for drop_dir in cdp-lane@.service.d web-fetcher.service.d cdp-ask.service.d; do
  mkdir -p "$USER_SYSTEMD/$drop_dir"
  cat >"$USER_SYSTEMD/$drop_dir/repo.conf" <<EOF
[Service]
Environment=ULG_REPO=$REPO
Environment=PYTHONPATH=$REPO/libs
EOF
done

for unit in jupiter-cdp-xvfb@.service cdp-lane@.service web-fetcher.service cdp-ask.service jupiter-cdp.target; do
  ln -sf "$SRC/$unit" "$USER_SYSTEMD/$unit"
done

ln -sf "$REPO/scripts/cdp-ask-start" "$LOCAL_BIN/cdp-ask-start"

cat >"$LOCAL_BIN/cdp-lane-launch" <<'WRAP'
#!/bin/sh
exec "$HOME/.venvs/universal/bin/python" "$ULG_REPO/services/jupiter-cdp/cdp-lane-launch" "$@"
WRAP
chmod +x "$LOCAL_BIN/cdp-lane-launch"

ln -sf "$SRC/cdp-lane-ensure" "$LOCAL_BIN/cdp-lane-ensure"

cat >"$LOCAL_BIN/jupiter-web-fetcher" <<'WRAP'
#!/bin/sh
exec "$HOME/.venvs/universal/bin/python" "$ULG_REPO/scripts/web-fetcher" --port 8765 --cdp-url http://127.0.0.1:9222
WRAP
chmod +x "$LOCAL_BIN/jupiter-web-fetcher"

python3 - "$SRC/pins.toml" "$USER_SYSTEMD" <<'PY'
import sys, tomllib
from pathlib import Path

pins_path, systemd_user = Path(sys.argv[1]), Path(sys.argv[2])
with pins_path.open("rb") as f:
    lanes = tomllib.load(f).get("lanes", {})
for name, row in lanes.items():
    display = str(row["display"]).lstrip(":")
    drop = systemd_user / f"cdp-lane@{name}.service.d"
    drop.mkdir(parents=True, exist_ok=True)
    (drop / "display.conf").write_text(
        f"[Unit]\nAfter=jupiter-cdp-xvfb@{display}.service\n"
        f"Upholds=jupiter-cdp-xvfb@{display}.service\n"
    )
PY

if [ -f "$HOME/.gateway/cdp-xvfb/env" ]; then
  rm -f "$HOME/.gateway/cdp-xvfb/env"
fi
if [ -f "$HOME/.profile" ]; then
  sed -i '/# CDP lanes — Xvfb (jupiter-cdp-xvfb)/d' "$HOME/.profile" || true
fi

systemctl --user daemon-reload
loginctl enable-linger "${USER:-krunch3r}"
systemctl --user enable jupiter-cdp.target

python3 - "$SRC/pins.toml" <<'PY'
import subprocess, sys, tomllib
with open(sys.argv[1], "rb") as f:
    lanes = tomllib.load(f).get("lanes", {})
for name, row in lanes.items():
    if row.get("standing"):
        subprocess.run(
            ["systemctl", "--user", "enable", f"cdp-lane@{name}.service"],
            check=True,
        )
PY

systemctl --user is-enabled jupiter-cdp.target || true
loginctl show-user "${USER:-krunch3r}" -p Linger || true

echo "install.sh complete (repo=$REPO)"
