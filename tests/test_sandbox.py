import os

import pytest

from pipez import config, sandbox as sbmod
from pipez.pve import PveError
from pipez.sandbox import MockSandbox, PveSandbox, SandboxError, compose_command


async def test_mock_run_and_files():
    sb = MockSandbox("r1")
    await sb.provision()
    try:
        r = await sb.run("echo hi; echo err >&2; pwd")
        assert r["rc"] == 0 and r["stdout"].startswith("hi") and "err" in r["stderr"]
        assert r["stdout"].strip().endswith(os.path.realpath(sb.workdir))
        assert set(r) == {"rc", "stdout", "stderr", "seconds"}
        assert (await sb.run("exit 3"))["rc"] == 3
        assert (await sb.run("cat", stdin="piped"))["stdout"] == "piped"
        await sb.put("a/b.txt", "hello")
        await sb.put("bin", b"\x00\x01")
        assert await sb.get("a/b.txt") == b"hello" and await sb.get("bin") == b"\x00\x01"
        assert (await sb.run("cat a/b.txt", cwd=sb.workdir))["stdout"] == "hello"
    finally:
        await sb.teardown(False)


async def test_secret_env_goes_to_dotenv_not_command():
    sb = MockSandbox("r2")
    await sb.provision()
    try:
        r = await sb.run('echo "$PLAIN:$MY_API_KEY:$X_SECRET:$GH_TOKEN"',
                         env={"PLAIN": "p", "MY_API_KEY": "k1-VALUE-4d5e6f", "X_SECRET": "s1-VALUE-7a8b9c",
                              "GH_TOKEN": "s3cr3t-VALUE-9f8e7d"})
        assert r["stdout"].strip() == "p:k1-VALUE-4d5e6f:s1-VALUE-7a8b9c:s3cr3t-VALUE-9f8e7d"
        line = sb.last_command
        assert "PLAIN=p" in line and ".env" in line
        for secret in ("k1-VALUE-4d5e6f", "s1-VALUE-7a8b9c", "s3cr3t-VALUE-9f8e7d", "MY_API_KEY=", "X_SECRET=", "GH_TOKEN="):
            assert secret not in line
        envfile = os.path.join(sb.workdir, ".env")
        assert oct(os.stat(envfile).st_mode & 0o777) == "0o600"
        assert "MY_API_KEY=k1-VALUE-4d5e6f" in open(envfile).read()
    finally:
        await sb.teardown(False)


def test_compose_command_quotes():
    line, secrets = compose_command("echo 'x y'", "/work/r", None, {"A": "b c"})
    assert secrets == {} and "A='b c'" in line and line.startswith("cd /work/r")


async def test_mock_teardown_keep_vs_destroy():
    a, b = MockSandbox("k1"), MockSandbox("k2")
    await a.provision(); await b.provision()
    await a.teardown(True)
    await b.teardown(False)
    assert os.path.isdir(a.workdir) and a.kept
    assert not os.path.exists(b.workdir)
    await a.teardown(False)
    assert not os.path.exists(a.workdir)


def test_make_sandbox(monkeypatch):
    monkeypatch.setattr(config, "MOCK", False)
    monkeypatch.setattr(config, "SANDBOX", "mock")
    assert isinstance(sbmod.make_sandbox("x"), MockSandbox)
    monkeypatch.setattr(config, "SANDBOX", "pve")
    assert isinstance(sbmod.make_sandbox("x", client=object()), PveSandbox)
    monkeypatch.setattr(config, "MOCK", True)
    assert isinstance(sbmod.make_sandbox("x"), MockSandbox)


class FakeClient:
    def __init__(self, ip_after=2):
        self.calls = []
        self.ip_after = ip_after
        self.agent_polls = 0
        self.cloned = []

    async def pool_members(self, pool):
        self.calls.append("pool_members"); return [9100] + self.cloned

    def free_vmid(self, rng, taken):
        self.calls.append("free_vmid")
        return 9101 if 9100 in taken else 9100

    async def clone(self, template, newid, name, pool):
        self.calls.append(("clone", template, newid, name, pool)); self.cloned.append(newid); return "UP-clone"

    async def wait_task(self, upid, timeout=300):
        self.calls.append(("wait", upid))

    async def set_config(self, vmid, **f):
        self.calls.append(("set_config", vmid, sorted(f), f["ciuser"])); 

    async def status(self, vmid):
        return {"status": "running"}

    async def start(self, vmid):
        self.calls.append("start"); return "UP-start"

    async def stop(self, vmid):
        self.calls.append("stop"); return "UP-stop"

    async def destroy(self, vmid):
        self.calls.append("destroy"); return "UP-destroy"

    async def agent_interfaces(self, vmid):
        self.agent_polls += 1
        if self.agent_polls <= self.ip_after:
            raise PveError(500, "agent not running")
        return [{"name": "lo", "ip-addresses": [{"ip-address": "127.0.0.1", "ip-address-type": "ipv4"}]},
                {"name": "eth0", "ip-addresses": [
                    {"ip-address": "fe80::1", "ip-address-type": "ipv6"},
                    {"ip-address": "192.168.50.77", "ip-address-type": "ipv4"}]}]


def make(client, tmp_path, monkeypatch, **kw):
    key = tmp_path / "k"
    (tmp_path / "k.pub").write_text("ssh-ed25519 AAA pipez\n")
    monkeypatch.setattr(config, "SSH_KEY", str(key))
    monkeypatch.setattr(config, "PVE_POOL", "pipez")
    monkeypatch.setattr(config, "PVE_TEMPLATE", 9001)
    monkeypatch.setattr(config, "PVE_VMID_RANGE", "9100-9199")
    sb = PveSandbox("abc", client, poll=0.001, **kw)
    ssh = []

    async def fake_ssh(remote, stdin=None, timeout=600):
        ssh.append(remote)
        return 0, b"", b""
    sb._ssh = fake_ssh
    return sb, ssh


async def test_pve_provision_order_and_ip_retry(tmp_path, monkeypatch):
    c = FakeClient(ip_after=2)
    sb, ssh = make(c, tmp_path, monkeypatch, boot_timeout=5)
    await sb.provision()
    assert sb.vmid == 9101 and sb.ip == "192.168.50.77"
    assert c.agent_polls == 3
    assert c.calls == [
        "pool_members", "free_vmid",
        ("clone", 9001, 9101, "pipez-abc", "pipez"), ("wait", "UP-clone"),
        ("set_config", 9101, ["ciuser", "ipconfig0", "sshkeys"], "etl"),
        "start", ("wait", "UP-start")]
    assert ssh == ["true", "sudo mkdir -p /work/abc && sudo chown etl:etl /work/abc"]
    await sb.teardown(False)
    assert c.calls[-4:] == ["stop", ("wait", "UP-stop"), "destroy", ("wait", "UP-destroy")]


async def test_pve_boot_timeout_destroys(tmp_path, monkeypatch):
    c = FakeClient(ip_after=10 ** 9)
    sb, _ = make(c, tmp_path, monkeypatch, boot_timeout=0.05)
    with pytest.raises(SandboxError, match="IPv4"):
        await sb.provision()
    assert "destroy" in c.calls   # a half-provisioned VM is not leaked


async def test_pve_keep_leaves_vm(tmp_path, monkeypatch):
    c = FakeClient(ip_after=0)
    sb, _ = make(c, tmp_path, monkeypatch, boot_timeout=5)
    await sb.provision()
    n = len(c.calls)
    await sb.teardown(True)
    assert sb.kept and len(c.calls) == n


async def test_cleanup_skips_when_vm_gone(tmp_path, monkeypatch):
    c = FakeClient(ip_after=0)

    async def boom(vmid, **f):
        raise PveError(400, "Parameter verification failed.")

    async def gone(vmid):
        raise PveError(403, "Permission check failed")
    c.set_config = boom
    c.status = gone
    sb, _ = make(c, tmp_path, monkeypatch, boot_timeout=5)
    with pytest.raises(SandboxError, match="Parameter verification"):
        await sb.provision()
    assert "destroy" not in c.calls


async def test_teardown_tolerates_403_404(tmp_path, monkeypatch):
    c = FakeClient(ip_after=0)
    sb, _ = make(c, tmp_path, monkeypatch, boot_timeout=5)
    await sb.provision()

    async def forbidden(vmid):
        raise PveError(403, "Permission check failed")
    c.destroy = forbidden
    await sb.teardown(False)   # must not raise
