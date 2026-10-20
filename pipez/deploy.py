"""Deploying a finished build to a lab VM (task 09).

`Deployer` runs the deployment steps against a Sandbox: a PveSandbox cloned
from the plain Ubuntu template (the VM is the deliverable and is kept), or a
MockDeploySandbox that does the same locally in a temp dir. The engine owns
the persistence; this module only reports progress through two callbacks.
"""
import asyncio
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import time

from . import config
from .sandbox import MockSandbox, PveSandbox, SandboxError

APP_USER = "etl"


class DeployError(Exception):
    pass


def app_name(plan: dict, run_id: str) -> str:
    """Top-level directory of the plan's repo_layout when obvious
    (`name/` on the first line), else app-<run_id>."""
    for line in (plan.get("repo_layout") or "").splitlines():
        line = line.strip().strip("`")
        if not line:
            continue
        m = re.fullmatch(r"(?:\./)?([A-Za-z0-9][A-Za-z0-9_.-]*)/(?:\s.*)?", line)
        if m and m.group(1) not in ("src", "tests", "test"):
            return m.group(1)
        break
    return "app-%s" % run_id


def start_command(deploy: dict) -> str:
    return deploy["start_command"].replace("{port}", str(deploy["port"]))


def systemd_unit(app: str, deploy: dict) -> str:
    cmd = start_command(deploy)
    # systemd quoting: \' inside single quotes; %% and $$ are literal % and $
    cmd = cmd.replace("\\", "\\\\").replace("'", "\\'").replace("%", "%%").replace("$", "$$")
    lines = ["[Unit]",
             "Description=%s (deployed by pipez)" % app,
             "After=network-online.target",
             "Wants=network-online.target",
             "",
             "[Service]",
             "User=%s" % APP_USER,
             "WorkingDirectory=/opt/%s" % app,
             "ExecStart=/bin/bash -lc '%s'" % cmd,
             "Restart=on-failure"]
    if deploy.get("env"):
        lines.append("EnvironmentFile=/opt/%s/.env" % app)
    lines += ["", "[Install]", "WantedBy=multi-user.target", ""]
    return "\n".join(lines)


def env_file(deploy: dict) -> str:
    return "".join("%s=%s\n" % (e["name"], e.get("value") or "") for e in deploy["env"])


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MockDeploySandbox(MockSandbox):
    """Local stand-in for the deploy VM: a temp dir, the service is a
    background process in its own process group (pid kept in a file so a
    restarted server can still stop it)."""

    def _pidfile(self) -> str:
        return os.path.join(self.workdir, "service.pid")

    def start_service(self, cmd: str, cwd: str, env: dict):
        log = open(os.path.join(self.workdir, "service.log"), "ab")
        proc = subprocess.Popen(["bash", "-lc", cmd], cwd=cwd, stdout=log, stderr=log,
                                stdin=subprocess.DEVNULL, start_new_session=True,
                                env=dict(os.environ, **env))
        log.close()
        with open(self._pidfile(), "w") as f:
            f.write(str(proc.pid))
        self._proc = proc
        return proc

    def stop_service(self):
        try:
            with open(self._pidfile()) as f:
                pid = int(f.read().strip())
            os.killpg(pid, signal.SIGKILL)
        except (OSError, ValueError):
            return
        proc = getattr(self, "_proc", None)
        try:
            if proc is not None:
                proc.wait(timeout=5)        # reap: the port is closed when it returns
            else:
                time.sleep(0.2)             # adopted after a restart: not our child
        except subprocess.TimeoutExpired:
            pass

    async def teardown(self, keep: bool):
        self.kept = keep
        if not keep and self.workdir:
            self.stop_service()
            shutil.rmtree(self.workdir, ignore_errors=True)


def make_deploy_sandbox(run_id: str, client=None):
    if config.MOCK or config.SANDBOX == "mock":
        return MockDeploySandbox(run_id)
    return PveSandbox(run_id, client, template=config.DEPLOY_TEMPLATE,
                      vmid_range=config.DEPLOY_VMID_RANGE, name_prefix="pipez-app-",
                      cores=2, memory=2048, boot_timeout=config.DEPLOY_BOOT_TIMEOUT)


class Deployer:
    """report(status, **row_fields) persists/publishes progress; emit(event,
    payload) writes task_log rows; note(line) appends to the deployment log."""

    def __init__(self, sb, run_id: str, plan: dict, tarball: str, report, emit, note,
                 health_timeout: float = None, poll: float = 3.0):
        self.sb, self.run_id, self.tarball = sb, run_id, tarball
        self.deploy = plan["deploy"]
        self.app = app_name(plan, run_id)
        self.report, self.emit, self.note = report, emit, note
        self.health_timeout = (config.DEPLOY_HEALTH_TIMEOUT if health_timeout is None
                               else health_timeout)
        self.mock = sb.kind == "mock"
        self.poll = 0.3 if self.mock else poll
        self.url = None

    @staticmethod
    def _tail(text: str, n: int = 1500) -> str:
        return text[-n:] if len(text) > n else text

    async def _sh(self, cmd: str, what: str, *, cwd=None, stdin=None, timeout=900):
        r = await self.sb.run(cmd, cwd=cwd, stdin=stdin, timeout=timeout)
        out = self._tail((r["stdout"] + r["stderr"]).strip())
        self.note("$ %s (rc %s)%s" % (cmd, r["rc"], "\n" + out if out else ""))
        if r["rc"]:
            raise DeployError("%s failed (rc %s): %s" % (what, r["rc"], out))
        return r

    async def run(self) -> str:
        sb, d, app = self.sb, self.deploy, self.app
        self.report("provisioning")
        await sb.provision()
        self.report("provisioning", vmid=sb.vmid, name=sb.name, ip=sb.ip)
        self.note("provisioned %s ip=%s" % (sb.name, sb.ip))

        self.report("installing")
        with open(self.tarball, "rb") as f:
            data = f.read()
        await sb.put("/tmp/app.tar.gz", data)
        if self.mock:
            appdir = os.path.join(sb.workdir, "opt", app)
            tmp = os.path.join(sb.workdir, "tmp", "app.tar.gz")
            await self._sh("mkdir -p %s && tar xzf %s -C %s" % (
                shlex.quote(appdir), shlex.quote(tmp), shlex.quote(appdir)),
                "extract tarball")
        else:
            appdir = "/opt/%s" % app
            q = shlex.quote(appdir)
            await self._sh("sudo mkdir -p %s && sudo tar xzf /tmp/app.tar.gz -C %s "
                           "&& sudo chown -R %s %s" % (q, q, APP_USER, q),
                           "extract tarball")
        if d["env"]:
            await sb.put(os.path.join(appdir, ".env"), env_file(d), 0o600)
        for cmd in d["install_commands"]:
            if self.mock and re.match(r"\s*(sudo|apt|apt-get)\b", cmd):
                self.emit("deploy_install", {"cmd": cmd, "rc": 0, "skipped": "mock"})
                self.note("$ %s (skipped in mock)" % cmd)
                continue
            r = await sb.run(cmd, cwd=appdir, timeout=1800)
            tail = self._tail((r["stdout"] + r["stderr"]).strip())
            self.emit("deploy_install", {"cmd": cmd, "rc": r["rc"],
                                         "seconds": r["seconds"], "tail": tail})
            self.note("$ %s (rc %s)%s" % (cmd, r["rc"], "\n" + tail if tail else ""))
            if r["rc"]:
                raise DeployError("install command `%s` exited %s:\n%s" % (cmd, r["rc"], tail))

        self.report("starting")
        port = d["port"]
        if self.mock:
            if not _port_free(port):
                port = _free_port()
                self.note("port %s busy, mock uses %s" % (d["port"], port))
            cmd = start_command(dict(d, port=port))
            sb.start_service(cmd, appdir, {e["name"]: e["value"] for e in d["env"]})
            self.note("started (mock): %s" % cmd)
        else:
            unit = systemd_unit(app, d)
            await self._sh("sudo tee /etc/systemd/system/%s.service >/dev/null" % app,
                           "write unit", stdin=unit)
            await self._sh("sudo systemctl daemon-reload && sudo systemctl enable --now %s"
                           % shlex.quote(app + ".service"), "start service")
        self.url = "http://%s:%s%s" % (sb.ip, port, d["health_path"])
        await self._health(self.url)
        return "http://%s:%s" % (sb.ip, port)

    async def _health(self, url: str):
        deadline = time.monotonic() + self.health_timeout
        last = ""
        while True:
            r = await self.sb.run("curl -sf -o /dev/null -m 10 %s" % shlex.quote(url),
                                  timeout=30)
            if r["rc"] == 0:
                self.note("health ok: %s" % url)
                return
            last = (r["stderr"] or r["stdout"]).strip()
            if time.monotonic() >= deadline:
                tail = ""
                if not self.mock:
                    j = await self.sb.run("sudo journalctl -u %s.service -n 30 --no-pager"
                                          % shlex.quote(self.app), timeout=30)
                    tail = self._tail((j["stdout"] + j["stderr"]).strip())
                elif self.sb.workdir:
                    try:
                        with open(os.path.join(self.sb.workdir, "service.log"), errors="replace") as f:
                            tail = self._tail(f.read())
                    except OSError:
                        pass
                raise DeployError("health check %s failed within %ss (curl rc %s %s)\n%s" % (
                    url, int(self.health_timeout), r["rc"], last, tail))
            await asyncio.sleep(self.poll)
