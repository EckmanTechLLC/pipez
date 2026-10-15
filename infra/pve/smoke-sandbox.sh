#!/usr/bin/env bash
# Smoke test: linked-clone 9001 via the scoped API token, SSH in, run checks, destroy.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Load only PIPEZ_PVE_* from .env (never echoed).
while IFS= read -r line; do
  [[ "$line" =~ ^(PIPEZ_PVE_[A-Z_]+)=(.*)$ ]] && export "${BASH_REMATCH[1]}=${BASH_REMATCH[2]}"
done < "$ROOT/.env"

KEY=~/.ssh/pipez_ed25519
BASE="$PIPEZ_PVE_URL/api2/json"
NODE="$PIPEZ_PVE_NODE"
AUTH="Authorization: PVEAPIToken=${PIPEZ_PVE_TOKEN_ID}=${PIPEZ_PVE_TOKEN_SECRET}"
CURL=(curl -sS --fail-with-body)
[[ "${PIPEZ_PVE_VERIFY_TLS:-1}" == "0" ]] && CURL+=(-k)
log() { echo "[$(date +%H:%M:%S)] $*"; }
api() { local m=$1 p=$2; shift 2; "${CURL[@]}" -X "$m" -H "$AUTH" "$@" "$BASE$p"; }

wait_task() { # upid
  local upid=$1 st
  for _ in $(seq 1 120); do
    st=$(api GET "/nodes/$NODE/tasks/$(jq -rn --arg u "$upid" '$u|@uri')/status") || return 1
    [[ "$(jq -r .data.status <<<"$st")" == "stopped" ]] && { [[ "$(jq -r .data.exitstatus <<<"$st")" == "OK" ]]; return; }
    sleep 2
  done
  return 1
}

VMID=""; RC=0
cleanup() {
  [[ -z "$VMID" ]] && return
  log "cleanup: stopping and destroying $VMID"
  local u
  u=$(api POST "/nodes/$NODE/qemu/$VMID/status/stop" 2>/dev/null | jq -r '.data // empty') && [[ -n "$u" ]] && wait_task "$u"
  u=$(api DELETE "/nodes/$NODE/qemu/$VMID?purge=1&destroy-unreferenced-disks=1" 2>/dev/null | jq -r '.data // empty')
  if [[ -n "$u" ]] && wait_task "$u"; then log "destroyed $VMID"; else log "WARNING: destroy of $VMID may have failed"; RC=1; fi
  return 0
}
finish() { local c=$?; cleanup; [[ $RC -ne 0 && $c -eq 0 ]] && c=$RC; exit $c; }
trap finish EXIT

LO=${PIPEZ_PVE_VMID_RANGE%-*}; HI=${PIPEZ_PVE_VMID_RANGE#*-}
used=$(api GET "/pools/$PIPEZ_PVE_POOL" | jq -r '.data.members[]?.vmid')
for id in $(seq "$LO" "$HI"); do grep -qx "$id" <<<"$used" || { NEW=$id; break; }; done
[[ -n "${NEW:-}" ]] || { echo "no free VMID" >&2; exit 1; }

T0=$SECONDS
log "linked-cloning $PIPEZ_PVE_TEMPLATE -> $NEW"
u=$(api POST "/nodes/$NODE/qemu/$PIPEZ_PVE_TEMPLATE/clone" \
    --data-urlencode "newid=$NEW" --data-urlencode "name=pipez-smoke-$(date +%s)" \
    --data-urlencode "pool=$PIPEZ_PVE_POOL" --data-urlencode "full=0" | jq -r .data) || { echo "clone failed" >&2; exit 1; }
VMID=$NEW
wait_task "$u" || { echo "clone task failed" >&2; exit 1; }

api PUT "/nodes/$NODE/qemu/$VMID/config" --data-urlencode "sshkeys=$(jq -rn --arg k "$(cat "$KEY.pub")" '$k|@uri')" \
    --data-urlencode "ciuser=etl" --data-urlencode "ipconfig0=ip=dhcp" >/dev/null || { echo "config failed" >&2; exit 1; }
u=$(api POST "/nodes/$NODE/qemu/$VMID/status/start" | jq -r .data) || { echo "start failed" >&2; exit 1; }
wait_task "$u" || { echo "start task failed" >&2; exit 1; }

log "waiting for guest agent + IPv4"
IP=""
for _ in $(seq 1 90); do
  r=$(api GET "/nodes/$NODE/qemu/$VMID/agent/network-get-interfaces" 2>/dev/null) && \
  IP=$(jq -r '[.data.result[] | select(.name=="eth0" or (.name|startswith("ens"))) | ."ip-addresses"[]? | select(."ip-address-type"=="ipv4") | ."ip-address"][0] // empty' <<<"$r")
  [[ -n "$IP" ]] && break
  sleep 2
done
[[ -n "$IP" ]] || { echo "no IP within 3 min" >&2; exit 1; }
log "guest IP $IP"

SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=5 -o BatchMode=yes "etl@$IP")
for _ in $(seq 1 30); do "${SSH[@]}" true 2>/dev/null && break; sleep 2; done
"${SSH[@]}" true 2>/dev/null || { echo "ssh not ready" >&2; exit 1; }
log "clone->SSH-ready: $((SECONDS-T0))s"

"${SSH[@]}" "python3 -m venv /tmp/vt && /tmp/vt/bin/pip --version && rm -rf /tmp/vt && python3.12 --version && node --version && claude --version && docker ps && /opt/pipez/venv/bin/python -c \"import claude_agent_sdk; print('sdk ok')\" && cat /etc/pipez-builder-version" || { echo "in-guest checks failed" >&2; RC=1; }
exit $RC
