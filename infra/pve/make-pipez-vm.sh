#!/usr/bin/env bash
# Create VM 9090 "pipez" (full clone of 9000) that hosts the pipez service.
# Usage: make-pipez-vm.sh [--force]    (run from the dev box; root SSH to the PVE host)
set -euo pipefail

HOST=root@192.168.50.19
VMID=9090
KEY=~/.ssh/pipez_ed25519
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

rmt() { ssh -o BatchMode=yes "$HOST" "$@"; }
log() { echo "[$(date +%H:%M:%S)] $*"; }

[[ -f "$KEY.pub" ]] || { echo "missing $KEY.pub (run make-builder-template.sh first)" >&2; exit 1; }

if rmt "qm status $VMID" >/dev/null 2>&1; then
  if [[ $FORCE -ne 1 ]]; then
    echo "VMID $VMID exists; use --force to destroy and recreate" >&2; exit 1
  fi
  log "destroying existing $VMID"
  rmt "qm stop $VMID --skiplock 1 >/dev/null 2>&1 || true; qm destroy $VMID --purge 1 --destroy-unreferenced-disks 1"
fi

log "cloning 9000 -> $VMID"
rmt "qm clone 9000 $VMID --name pipez --full 1"
KT=/tmp/pipez-vm-keys.$$
{ rmt "cat /root/.ssh/id_*.pub 2>/dev/null | grep -v '^$' || true"; cat "$KEY.pub" ~/.ssh/id_ed25519.pub; } > "$KT"
scp -q "$KT" "$HOST:/tmp/pipez-vm-keys"; rm -f "$KT"
rmt "qm set $VMID --cores 4 --memory 8192 --cpu host --ciuser etl --ipconfig0 ip=dhcp --sshkeys /tmp/pipez-vm-keys && rm -f /tmp/pipez-vm-keys && qm disk resize $VMID scsi0 40G"

log "starting $VMID"
rmt "qm start $VMID"
IP=""
for i in $(seq 1 90); do
  r=$(rmt "qm guest cmd $VMID network-get-interfaces 2>/dev/null" || true)
  IP=$(jq -r '[.[] | select(.name!="lo") | ."ip-addresses"[]? | select(."ip-address-type"=="ipv4") | ."ip-address"][0] // empty' <<<"${r:-[]}" 2>/dev/null || true)
  [[ -n "$IP" ]] && break
  log "waiting for guest agent/IP ($i)"; sleep 5
done
[[ -n "$IP" ]] || { echo "no IP within 7.5 min" >&2; exit 1; }
log "guest IP $IP"

rmt "pvesh set /pools/pipez -vms $VMID"

SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=5 -o BatchMode=yes "etl@$IP")
for _ in $(seq 1 60); do "${SSH[@]}" true 2>/dev/null && break; sleep 3; done
"${SSH[@]}" true || { echo "ssh not ready" >&2; exit 1; }
log "waiting for cloud-init"
"${SSH[@]}" "sudo cloud-init status --wait" || true

log "installing python3.12, git, rsync"
"${SSH[@]}" 'set -e
  export DEBIAN_FRONTEND=noninteractive
  if ! command -v python3.12 >/dev/null 2>&1 || ! python3.12 -c "import venv, ensurepip" 2>/dev/null; then
    for _ in $(seq 1 30); do sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 || break; sleep 5; done
    sudo apt-get update -qq
    sudo apt-get install -y -qq software-properties-common
    sudo add-apt-repository -y ppa:deadsnakes/ppa
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3.12 python3.12-venv git rsync curl
  fi
  sudo mkdir -p /opt/pipez && sudo chown etl:etl /opt/pipez
  python3.12 --version; git --version | head -1; rsync --version | head -1'

printf 'PIPEZ_VM_IP=%s\nPIPEZ_VM_ID=%s\n' "$IP" "$VMID" > "$DIR/pipez-vm.env"
log "pipez VM $VMID ready at $IP (recorded in infra/pve/pipez-vm.env)"
rmt "qm config $VMID"
