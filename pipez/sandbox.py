"""Per-run sandboxes: a Proxmox VM (PveSandbox) or a local temp dir
(MockSandbox) behind one interface. No agent code runs here - this is only
lifecycle, command execution and file transfer.
"""
import asyncio
import os
import posixpath
import shlex
import shutil
import tempfile
import logging
import time

from . import config
from .pve import PveError, ProxmoxClient

SECRET_SUFFIXES = ("_KEY", "SECRET", "TOKEN")
log = logging.getLogger(__name__)
_reserved: set[int] = set()   # vmids picked by in-flight provisions (this process)


class SandboxError(Exception):
    pass


def compose_command(cmd: str, workdir: str, cwd: str = None, env: dict = None):
    """Build the shell line to run. Returns (line, secrets). Plain env goes on
    the line via `env K=V`; secret-looking keys are returned separately and
    the line sources workdir/.env instead, so they never appear in argv."""
    env = env or {}
    secrets = {k: v for k, v in env.items() if k.endswith(SECRET_SUFFIXES)}
    plain = {k: v for k, v in env.items() if k not in secrets}
    parts = ["cd %s" % shlex.quote(cwd or workdir)]
    if secrets:
        parts.append("set -a; . %s; set +a" % shlex.quote(posixpath.join(workdir, ".env")))
    prefix = "env " + " ".join("%s=%s" % (k, shlex.quote(str(v)))
                               for k, v in plain.items()) + " " if plain else ""
    parts.append(prefix + "bash -lc " + shlex.quote(cmd))
    return " && ".join(parts), secrets


def _env_file(secrets: dict) -> str:
    return "".join("%s=%s\n" % (k, shlex.quote(str(v))) for k, v in sorted(secrets.items()))


class Sandbox:
    kind = "base"
    vmid = None
    ip = None
    name = None
    workdir = None
    kept = False

    def __init__(self, run_id: str):
        self.run_id = run_id
        self._secrets: dict = {}
        self.last_command = None   # hook: the most recently composed line

    async def provision(self): raise NotImplementedError

    async def run(self, cmd: str, *, cwd=None, env: dict = None, stdin: str = None,
                  timeout=600) -> dict:
        line, secrets = compose_command(cmd, self.workdir, cwd, env)
        self.last_command = line
        if secrets and secrets != self._secrets:
            await self.put(posixpath.join(self.workdir, ".env"), _env_file(secrets), 0o600)
            self._secrets = secrets
        t0 = time.monotonic()
        rc, out, err = await self._exec(line, stdin, timeout)
        return {"rc": rc, "stdout": out.decode(errors="replace"),
                "stderr": err.decode(errors="replace"),
                "seconds": round(time.monotonic() - t0, 3)}

    async def _exec(self, line: str, stdin, timeout) -> tuple:
        raise NotImplementedError

    async def git(self, args, cwd=None, **kw) -> dict:
        """`git <args>` via run(); args is a list (quoted here) or a string."""
        if not isinstance(args, str):
            args = " ".join(shlex.quote(str(a)) for a in args)
        return await self.run("git " + args, cwd=cwd, **kw)

    # Coding-leaf runner: script to upload and interpreter to run it with.
    runner_script = "pipez_runner.py"
    python = "/opt/pipez/venv/bin/python"

    async def put(self, path: str, content, mode: int = 0o644): raise NotImplementedError
    async def get(self, path: str) -> bytes: raise NotImplementedError
    async def teardown(self, keep: bool): raise NotImplementedError

    def adopt(self, row: dict):
        """Attach to an existing sandbox described by a sandboxes row."""
        self.vmid, self.ip, self.name = row.get("vmid"), row.get("ip"), row.get("name")

    async def alive(self) -> bool:
        return False

    @staticmethod
    def _bytes(content) -> bytes:
        return content.encode() if isinstance(content, str) else content


async def _spawn(argv, stdin, timeout) -> tuple:
    proc = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(stdin.encode() if isinstance(stdin, str) else stdin), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, b"", b"timed out after %ss" % str(timeout).encode()
    except asyncio.CancelledError:      # run stopped: do not leave the child running
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode, out, err


class MockSandbox(Sandbox):
    kind = "mock"
    runner_script = "mock_runner.py"
    python = "python3"

    async def provision(self):
        await asyncio.sleep(0.2)
        self.workdir = tempfile.mkdtemp(prefix="pipez-sandbox-%s-" % self.run_id)
        self.name, self.ip = self.workdir, "127.0.0.1"

    def adopt(self, row: dict):
        super().adopt(row)
        self.workdir = row.get("name")

    async def alive(self) -> bool:
        return bool(self.workdir) and os.path.isdir(self.workdir)

    def _path(self, path: str) -> str:
        if path == self.workdir or path.startswith(self.workdir.rstrip("/") + "/"):
            return path
        return os.path.join(self.workdir, path.lstrip("/"))

    async def _exec(self, line, stdin, timeout):
        return await _spawn(["bash", "-c", line], stdin, timeout)

    async def put(self, path, content, mode=0o644):
        p = self._path(path)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(self._bytes(content))
        os.chmod(p, mode)

    async def get(self, path) -> bytes:
        with open(self._path(path), "rb") as f:
            return f.read()

    async def teardown(self, keep: bool):
        self.kept = keep
        if not keep and self.workdir and not config.SANDBOX_KEEP:
            shutil.rmtree(self.workdir, ignore_errors=True)


class PveSandbox(Sandbox):
    kind = "pve"
    SSH_OPTS = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
                "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "LogLevel=ERROR"]

    def __init__(self, run_id: str, client: ProxmoxClient = None,
                 boot_timeout: float = None, poll: float = 2.0, *,
                 template: int = None, vmid_range: str = None,
                 name_prefix: str = "pipez-", cores: int = None,
                 memory: int = None):
        super().__init__(run_id)
        self.template, self.vmid_range = template, vmid_range   # None -> config
        self.cores, self.memory = cores, memory
        self.client = client or ProxmoxClient()
        self.boot_timeout = config.SANDBOX_BOOT_TIMEOUT if boot_timeout is None else boot_timeout
        self.poll = poll
        self.workdir = "/work/%s" % run_id
        self.name = "%s%s" % (name_prefix, run_id)
        self.user = "etl"

    # -- ssh ---------------------------------------------------------------

    def _argv(self, remote: str) -> list:
        return (["ssh", "-i", config.SSH_KEY] + self.SSH_OPTS
                + ["%s@%s" % (self.user, self.ip), remote])

    async def _ssh(self, remote: str, stdin=None, timeout=600) -> tuple:
        return await _spawn(self._argv(remote), stdin, timeout)

    async def _exec(self, line, stdin, timeout):
        return await self._ssh(line, stdin, timeout)

    async def put(self, path, content, mode=0o644):
        p = posixpath.join(self.workdir, path)
        rc, _, err = await self._ssh(
            "mkdir -p %s && cat > %s && chmod %o %s" % (
                shlex.quote(posixpath.dirname(p)), shlex.quote(p), mode, shlex.quote(p)),
            self._bytes(content), 120)
        if rc:
            raise SandboxError("put %s failed: %s" % (p, err.decode(errors="replace")))

    async def get(self, path) -> bytes:
        p = posixpath.join(self.workdir, path)
        rc, out, err = await self._ssh("cat %s" % shlex.quote(p), None, 120)
        if rc:
            raise SandboxError("get %s failed: %s" % (p, err.decode(errors="replace")))
        return out

    # -- lifecycle ---------------------------------------------------------

    async def provision(self):
        c = self.client
        try:
            taken = set(await c.pool_members(config.PVE_POOL)) | _reserved
            self.vmid = c.free_vmid(self.vmid_range or config.PVE_VMID_RANGE, taken)
            _reserved.add(self.vmid)
        except PveError as e:
            raise SandboxError("vmid allocation failed: %s" % e)
        cloned = False
        try:
            upid = await c.clone(self.template or config.PVE_TEMPLATE, self.vmid, self.name, config.PVE_POOL)
            cloned = True
            await c.wait_task(upid)
            with open(config.SSH_KEY + ".pub") as f:
                pubkey = f.read().strip()
            sizing = {k: v for k, v in (("cores", self.cores),
                                        ("memory", self.memory)) if v}
            await c.set_config(self.vmid, sshkeys=pubkey, ciuser=self.user,
                               ipconfig0="ip=dhcp", **sizing)
            await c.wait_task(await c.start(self.vmid))
            self.ip = await self._discover_ip()
            await self._wait_ssh()
            wd = shlex.quote(self.workdir)   # /work is root-owned in the template
            rc, _, err = await self._ssh(
                "sudo mkdir -p %s && sudo chown %s:%s %s" % (wd, self.user, self.user, wd),
                None, 60)
            if rc:
                raise SandboxError("mkdir workdir failed: %s" % err.decode(errors="replace"))
        except BaseException as e:
            if cloned:
                await self._destroy_quietly()
            _reserved.discard(self.vmid)
            if isinstance(e, SandboxError):
                raise
            if isinstance(e, (PveError, OSError)):
                raise SandboxError("provision failed: %s" % e)
            raise

    async def _discover_ip(self) -> str:
        deadline = time.monotonic() + self.boot_timeout
        while True:
            try:
                for iface in await self.client.agent_interfaces(self.vmid):
                    for a in iface.get("ip-addresses") or []:
                        ip = a.get("ip-address", "")
                        if (a.get("ip-address-type") == "ipv4"
                                and not ip.startswith(("127.", "169.254."))):
                            return ip
            except PveError:
                pass   # agent not up yet (404/500)
            if time.monotonic() >= deadline:
                raise SandboxError("no IPv4 from guest agent within %ss" % self.boot_timeout)
            await asyncio.sleep(self.poll)

    async def _wait_ssh(self):
        deadline = time.monotonic() + self.boot_timeout
        while True:
            rc, _, _ = await self._ssh("true", None, 20)
            if rc == 0:
                return
            if time.monotonic() >= deadline:
                raise SandboxError("ssh not ready within %ss" % self.boot_timeout)
            await asyncio.sleep(self.poll)

    async def _destroy_quietly(self):
        try:
            try:
                await self.client.status(self.vmid)
                in_pool = self.vmid in await self.client.pool_members(config.PVE_POOL)
            except PveError as e:
                log.warning("sandbox_cleanup_skipped vmid=%s: not found (%s)", self.vmid, e)
                return
            if not in_pool:
                log.warning("sandbox_cleanup_skipped vmid=%s: not in pool %s",
                            self.vmid, config.PVE_POOL)
                return
            await self._stop()
            await self.client.wait_task(await self.client.destroy(self.vmid))
        except Exception as e:
            log.warning("sandbox_cleanup_failed vmid=%s: %s", self.vmid, e)

    async def _stop(self):
        try:
            await self.client.wait_task(await self.client.stop(self.vmid))
        except PveError:
            pass   # already stopped / gone

    async def alive(self) -> bool:
        try:
            return (await self.client.status(self.vmid)).get("status") == "running"
        except PveError:
            return False

    async def teardown(self, keep: bool):
        self.kept = keep
        if keep:
            return
        if self.vmid is None:
            raise SandboxError("no vmid to destroy")
        try:
            await self._stop()
            await self.client.wait_task(await self.client.destroy(self.vmid))
        except PveError as e:
            if e.status in (403, 404):
                log.warning("sandbox_teardown_warning vmid=%s: %s", self.vmid, e)
                return
            raise SandboxError("teardown failed: %s" % e)
        finally:
            _reserved.discard(self.vmid)


def make_sandbox(run_id: str, client: ProxmoxClient = None) -> Sandbox:
    if config.MOCK or config.SANDBOX == "mock":
        return MockSandbox(run_id)
    return PveSandbox(run_id, client)
