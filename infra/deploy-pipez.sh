#!/usr/bin/env bash
# Deploy the working tree to the pipez VM (idempotent). Run from the dev box as etl.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/infra/pve/pipez-vm.env"
KEY=~/.ssh/pipez_ed25519
SSHO=(-i "$KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=10 -o BatchMode=yes)
T="etl@$PIPEZ_VM_IP"
log() { echo "[$(date +%H:%M:%S)] $*"; }
vm() { ssh "${SSHO[@]}" "$T" "$@"; }

[[ -f "$ROOT/.env" ]] || { echo "missing $ROOT/.env" >&2; exit 1; }
vm 'mkdir -p /opt/pipez/app /opt/pipez/data/artifacts ~/.ssh && chmod 700 ~/.ssh'

log "rsync tree -> $T:/opt/pipez/app/"
rsync -az --delete -e "ssh ${SSHO[*]}" \
  --exclude .venv --exclude .git --exclude .odin/index-cache.json \
  --exclude 'pipez.sqlite3*' --exclude pipez.log --exclude artifacts/ \
  --exclude __pycache__ --exclude .pytest_cache --exclude infra/pve/pipez-vm.env \
  --exclude .env \
  "$ROOT/" "$T:/opt/pipez/app/"

log "installing .env (mode 600) with VM overrides"
TMP="$(mktemp)"; trap 'rm -f "$TMP"' EXIT
chmod 600 "$TMP"
grep -vE '^(PIPEZ_HOST|PIPEZ_PORT|PIPEZ_DB|PIPEZ_ARTIFACTS_DIR)=' "$ROOT/.env" > "$TMP" || true
[[ -n "$(tail -c1 "$TMP")" ]] && echo >> "$TMP"
cat >> "$TMP" <<ENV
PIPEZ_HOST=0.0.0.0
PIPEZ_PORT=8400
PIPEZ_DB=/opt/pipez/data/pipez.sqlite3
PIPEZ_ARTIFACTS_DIR=/opt/pipez/data/artifacts
ENV
scp -q "${SSHO[@]}" "$TMP" "$T:/opt/pipez/app/.env"
vm 'chmod 600 /opt/pipez/app/.env'
scp -q "${SSHO[@]}" "$KEY" "$T:/home/etl/.ssh/pipez_ed25519"
scp -q "${SSHO[@]}" "$KEY.pub" "$T:/home/etl/.ssh/pipez_ed25519.pub"
vm 'chmod 600 /home/etl/.ssh/pipez_ed25519; chmod 644 /home/etl/.ssh/pipez_ed25519.pub'

log "venv + requirements"
vm '[ -x /opt/pipez/venv/bin/python ] || python3.12 -m venv /opt/pipez/venv
    /opt/pipez/venv/bin/pip install -q --upgrade pip
    /opt/pipez/venv/bin/pip install -q -r /opt/pipez/app/requirements.txt'

log "systemd unit"
vm 'sudo tee /etc/systemd/system/pipez.service >/dev/null' <<'UNIT'
[Unit]
Description=pipez
After=network-online.target
Wants=network-online.target

[Service]
User=etl
WorkingDirectory=/opt/pipez/app
ExecStart=/opt/pipez/venv/bin/python run.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
vm 'sudo systemctl daemon-reload && sudo systemctl enable --now pipez && sudo systemctl restart pipez'

URL="http://$PIPEZ_VM_IP:8400"
for _ in $(seq 1 40); do curl -sf "$URL/api/runs" >/dev/null && { log "pipez up: $URL/"; exit 0; }; sleep 2; done
echo "pipez did not answer at $URL/api/runs" >&2
vm 'sudo journalctl -u pipez -n 30 --no-pager' >&2 || true
exit 1
