"""Minimal async Proxmox VE API client (scoped API token, pool-limited).

Only the calls the sandbox lifecycle needs. The token comes from config as a
ready-made header value and is never logged.
"""
import asyncio
from urllib.parse import quote

import httpx2 as httpx

from . import config

RETRIES = 3          # retries after the first attempt, on transient errors


class PveError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__("PVE %s: %s" % (status, message))
        self.status = status
        self.message = message


class PveTransientError(PveError):
    """5xx or transport failure - retried before surfacing."""


class ProxmoxClient:
    def __init__(self, url=None, node=None, auth_header=None, verify=None,
                 transport=None, backoff: float = 0.5):
        self.node = node if node is not None else config.PVE_NODE
        self.backoff = backoff
        self._http = httpx.AsyncClient(
            base_url=(url if url is not None else config.PVE_URL) + "/api2/json",
            headers={"Authorization": auth_header if auth_header is not None
                     else config.PVE_AUTH_HEADER},
            verify=config.PVE_VERIFY_TLS if verify is None else verify,
            transport=transport, timeout=30)

    async def aclose(self):
        await self._http.aclose()

    async def _req(self, method: str, path: str, form: dict = None):
        kwargs = {}
        if form:
            body = "&".join("%s=%s" % (quote(str(k), safe=""), quote(str(v), safe=""))
                            for k, v in form.items())
            kwargs = {"content": body, "headers": {
                "Content-Type": "application/x-www-form-urlencoded"}}
        last = None
        for attempt in range(RETRIES + 1):
            if attempt:
                await asyncio.sleep(self.backoff * 2 ** (attempt - 1))
            try:
                r = await self._http.request(method, path, **kwargs)
            except httpx.TransportError as e:
                last = PveTransientError(0, "%s: %s" % (type(e).__name__, e))
                continue
            if r.status_code >= 500:
                last = PveTransientError(r.status_code, self._err_text(r, "server error"))
                continue
            if r.status_code >= 400:
                raise PveError(r.status_code, self._err_text(r, "error"))
            try:
                return r.json().get("data")
            except ValueError:
                return None
        raise last

    @staticmethod
    def _err_text(r, default: str) -> str:
        """Proxmox error detail (`message` / `errors`) or the reason phrase."""
        text = r.reason_phrase or default
        try:
            body = r.json()
        except ValueError:
            return text
        if not isinstance(body, dict):
            return text
        parts = []
        if body.get("message"):
            parts.append(str(body["message"]).strip())
        if isinstance(body.get("errors"), dict):
            parts += ["%s: %s" % (k, v) for k, v in body["errors"].items()]
        return "; ".join(parts) or text

    def _vm(self, vmid: int, tail: str = "") -> str:
        return "/nodes/%s/qemu/%s%s" % (self.node, vmid, tail)

    async def clone(self, template: int, newid: int, name: str, pool: str) -> str:
        return await self._req("POST", self._vm(template, "/clone"), {
            "newid": newid, "name": name, "pool": pool, "full": 0})

    async def wait_task(self, upid: str, timeout: float = 300, poll: float = 1.0):
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        path = "/nodes/%s/tasks/%s/status" % (self.node, quote(upid, safe=""))
        while True:
            st = await self._req("GET", path) or {}
            if st.get("status") == "stopped":
                if st.get("exitstatus") != "OK":
                    raise PveError(0, "task failed: %s" % st.get("exitstatus"))
                return st
            if loop.time() >= deadline:
                raise PveError(0, "task timed out: %s" % upid)
            await asyncio.sleep(poll)

    async def set_config(self, vmid: int, **fields):
        # Proxmox wants sshkeys URL-encoded as a value, on top of form encoding
        # (matches infra/pve/smoke-sandbox.sh: jq @uri, then --data-urlencode).
        if "sshkeys" in fields:
            fields["sshkeys"] = quote(str(fields["sshkeys"]), safe="")
        return await self._req("PUT", self._vm(vmid, "/config"), fields)

    async def start(self, vmid: int) -> str:
        return await self._req("POST", self._vm(vmid, "/status/start"))

    async def stop(self, vmid: int) -> str:
        return await self._req("POST", self._vm(vmid, "/status/stop"))

    async def destroy(self, vmid: int) -> str:
        return await self._req(
            "DELETE", self._vm(vmid) + "?purge=1&destroy-unreferenced-disks=1")

    async def status(self, vmid: int) -> dict:
        return await self._req("GET", self._vm(vmid, "/status/current")) or {}

    async def agent_interfaces(self, vmid: int) -> list:
        data = await self._req("GET", self._vm(vmid, "/agent/network-get-interfaces"))
        return (data or {}).get("result") or []

    async def pool_members(self, pool: str) -> list:
        data = await self._req("GET", "/pools/%s" % quote(pool, safe="")) or {}
        return [int(m["vmid"]) for m in data.get("members") or [] if "vmid" in m]

    @staticmethod
    def free_vmid(range_str: str, taken) -> int:
        lo, _, hi = range_str.partition("-")
        lo, hi = int(lo), int(hi or lo)
        taken = set(int(t) for t in taken)
        for vmid in range(lo, hi + 1):
            if vmid not in taken:
                return vmid
        raise PveError(0, "no free VMID in %s" % range_str)
