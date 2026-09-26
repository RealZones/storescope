#!/usr/bin/env bash
# One-shot install on a Debian/Ubuntu VPS: creates a user, venv, systemd service.
# Usage: sudo bash deploy/install.sh   (run from the cloned repo directory)
set -euo pipefail
SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST=/opt/dragtrack

apt-get update -qq && apt-get install -y -qq python3 python3-venv rsync >/dev/null
id -u dragtrack >/dev/null 2>&1 || useradd --system --home "$DEST" --shell /usr/sbin/nologin dragtrack
mkdir -p "$DEST"
rsync -a --exclude '.venv' --exclude 'data' --exclude '.git' --exclude '__pycache__' "$SRC/" "$DEST/"
mkdir -p "$DEST/data"
[ -f "$DEST/.env" ] || cp "$DEST/.env.example" "$DEST/.env"
python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install --quiet --upgrade pip
"$DEST/.venv/bin/pip" install --quiet -r "$DEST/requirements.txt"
chown -R dragtrack:dragtrack "$DEST"
cp "$DEST/deploy/dragtrack.service" /etc/systemd/system/dragtrack.service
systemctl daemon-reload
systemctl enable --now dragtrack
sleep 3
systemctl --no-pager --lines=5 status dragtrack || true
echo
echo "DragTrack is running on port ${PORT:-8787}. Edit $DEST/.env (set DASHBOARD_PASSWORD!) then: sudo systemctl restart dragtrack"
