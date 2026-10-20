#!/usr/bin/env bash
# Build template 9002 "pipez-app-base" (deploy VMs) on the Proxmox host from template 9000.
# Usage: make-app-template.sh [--force]
set -euo pipefail

HOST=root@192.168.50.19
VMID=9002
KEY=~/.ssh/pipez_ed25519
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

rmt() { ssh -o BatchMode=yes "$HOST" "$@"; }
log() { echo "[$(date +%H:%M:%S)] $*"; }

# 1. keypair
if [[ ! -f "$KEY" ]]; then
  log "generating $KEY"
  ssh-keygen -t ed25519 -N '' -C pipez -f "$KEY" >/dev/null
fi
chmod 600 "$KEY"; chmod 644 "$KEY.pub"

# 2. snippets
log "copying snippets"
scp -q "$DIR/pipez-app-vendor.yaml" "$DIR/pipez-app-clone-vendor.yaml" "$HOST:/var/lib/vz/snippets/"

# 3. existing VM?
if rmt "qm status $VMID" >/dev/null 2>&1; then
  if [[ $FORCE -ne 1 ]]; then
    echo "VMID $VMID exists; use --force to destroy and rebuild" >&2; exit 1
  fi
  log "destroying existing $VMID"
  rmt "qm stop $VMID --skiplock 1 >/dev/null 2>&1 || true; qm destroy $VMID --purge 1 --destroy-unreferenced-disks 1"
fi

# 4. clone + configure
log "cloning 9000 -> $VMID"
rmt "qm clone 9000 $VMID --name pipez-app-base --full 1"
{ rmt "cat /root/.ssh/id_*.pub 2>/dev/null | grep -v '^$' || true"; cat "$KEY.pub"; } > /tmp/pipez-app-keys.$$
scp -q /tmp/pipez-app-keys.$$ "$HOST:/tmp/pipez-app-keys"
rm -f /tmp/pipez-app-keys.$$
rmt "qm set $VMID --cores 2 --memory 2048 --cicustom vendor=local:snippets/pipez-app-vendor.yaml --ciuser etl --ipconfig0 ip=dhcp --sshkeys /tmp/pipez-app-keys && rm -f /tmp/pipez-app-keys"

# 5. start + wait
log "starting $VMID"
rmt "qm start $VMID"
for i in $(seq 1 60); do
  rmt "qm agent $VMID ping" >/dev/null 2>&1 && break
  log "waiting for guest agent ($i)"; sleep 5
done
log "waiting for cloud-init (ceiling 10 min)"
START=$SECONDS
while :; do
  if (( SECONDS - START > 600 )); then echo "cloud-init timeout" >&2; exit 1; fi
  out=$(rmt "timeout 60 qm guest exec $VMID --timeout 30 -- cloud-init status 2>&1" || true)
  if grep -q '"out-data"' <<<"$out"; then
    if grep -q 'status: done' <<<"$out"; then break; fi
    if grep -q 'status: error' <<<"$out"; then echo "cloud-init error: $out" >&2; exit 1; fi
  fi
  log "cloud-init running... $((SECONDS-START))s"
  sleep 15
done
log "cloud-init finished"

# 6. verify inside guest
for c in "systemctl is-active qemu-guest-agent" "python3 -m venv /tmp/v && /tmp/v/bin/python --version && rm -rf /tmp/v" \
         "curl --version | head -1" "rsync --version | head -1"; do
  log "verify: $c"
  b=$(printf '%s' "$c" | base64 -w0)
  r=$(rmt "qm guest exec $VMID --timeout 60 -- bash -c \"echo $b | base64 -d | bash\"")
  echo "$r" | jq -r '."out-data" // empty, ."err-data" // empty'
  [[ "$(echo "$r" | jq -r '.exitcode')" == "0" ]] || { echo "verify failed: $c" >&2; exit 1; }
done

# 7. generalise + template
log "cleaning and shutting down"
rmt "qm guest exec $VMID --timeout 120 -- bash -c 'apt-get clean; cloud-init clean --logs --machine-id' >/dev/null"
rmt "qm shutdown $VMID --timeout 120" || rmt "qm stop $VMID"
rmt "qm wait $VMID --timeout 60 2>/dev/null || true"
rmt "qm set $VMID --cicustom vendor=local:snippets/pipez-app-clone-vendor.yaml"
rmt "qm template $VMID"
# the pipez token must be able to clone the template (ACL is path-based; idempotent)
rmt "pveum acl modify /vms/$VMID --roles PVETemplateUser --users pipez@pve --tokens 'pipez@pve!builder'"
log "template $VMID ready"
rmt "qm config $VMID"
