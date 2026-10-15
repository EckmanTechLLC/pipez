#!/usr/bin/env bash
# Build template 9001 "pipez-builder" on the Proxmox host from template 9000.
# Usage: make-builder-template.sh [--force]
set -euo pipefail

HOST=root@192.168.50.19
VMID=9001
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
scp -q "$DIR/pipez-builder-vendor.yaml" "$DIR/pipez-sandbox-vendor.yaml" "$HOST:/var/lib/vz/snippets/"

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
rmt "qm clone 9000 $VMID --name pipez-builder --full 1"
{ rmt "cat /root/.ssh/id_*.pub 2>/dev/null | grep -v '^$' || true"; cat "$KEY.pub"; } > /tmp/pipez-builder-keys.$$
scp -q /tmp/pipez-builder-keys.$$ "$HOST:/tmp/pipez-builder-keys"
rm -f /tmp/pipez-builder-keys.$$
rmt "qm set $VMID --cores 4 --memory 8192 --cpu host --cicustom vendor=local:snippets/pipez-builder-vendor.yaml --ciuser etl --ipconfig0 ip=dhcp --sshkeys /tmp/pipez-builder-keys && rm -f /tmp/pipez-builder-keys && qm disk resize $VMID scsi0 40G"

# 5. start + wait
log "starting $VMID"
rmt "qm start $VMID"
for i in $(seq 1 60); do
  rmt "qm agent $VMID ping" >/dev/null 2>&1 && break
  log "waiting for guest agent ($i)"; sleep 5
done
# NB: the agent may come up (from the base image) before the bake finishes; cloud-init status --wait covers that.
log "waiting for cloud-init (ceiling 20 min)"
START=$SECONDS
while :; do
  if (( SECONDS - START > 1200 )); then echo "cloud-init timeout" >&2; exit 1; fi
  out=$(rmt "timeout 60 qm guest exec $VMID --timeout 30 -- cloud-init status 2>&1" || true)
  if grep -q '"out-data"' <<<"$out"; then
    if grep -q 'status: done' <<<"$out"; then break; fi
    if grep -q 'status: error' <<<"$out"; then echo "cloud-init error: $out" >&2; exit 1; fi
  fi
  log "cloud-init running... $((SECONDS-START))s"
  sleep 20
done
log "cloud-init finished"

# 6. verify inside guest
for c in "python3.12 --version" "python3 -m venv /tmp/vt && /tmp/vt/bin/pip --version && rm -rf /tmp/vt" "node --version" "docker --version" "claude --version" \
         "/opt/pipez/venv/bin/python -c 'import claude_agent_sdk; print(\"sdk ok\")'" \
         "cat /etc/pipez-builder-version"; do
  log "verify: $c"
  b=$(printf '%s' "$c" | base64 -w0)
  r=$(rmt "qm guest exec $VMID --timeout 60 -- bash -c \"echo $b | base64 -d | bash\"")
  echo "$r" | jq -r '."out-data" // empty, ."err-data" // empty'
  [[ "$(echo "$r" | jq -r '.exitcode')" == "0" ]] || { echo "verify failed: $c" >&2; exit 1; }
done

# 7. generalise + template
log "cleaning and shutting down"
rmt "qm guest exec $VMID --timeout 120 -- bash -c 'apt-get clean; cloud-init clean --logs --machine-id; rm -f /var/log/pipez-bake.log' >/dev/null"
rmt "qm shutdown $VMID --timeout 120" || rmt "qm stop $VMID"
rmt "qm wait $VMID --timeout 60 2>/dev/null || true"
rmt "qm set $VMID --cicustom vendor=local:snippets/pipez-sandbox-vendor.yaml"
rmt "qm template $VMID"
# the pipez token must be able to clone the template (ACL is path-based; idempotent)
rmt "pveum acl modify /vms/$VMID --roles PVETemplateUser --users pipez@pve --tokens 'pipez@pve!builder'"
log "template $VMID ready"
rmt "qm config $VMID"
