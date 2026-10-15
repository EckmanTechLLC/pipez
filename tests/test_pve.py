import httpx2 as httpx
import pytest

from pipez.pve import PveError, PveTransientError, ProxmoxClient


def client(handler, **kw):
    return ProxmoxClient(url="https://pve:8006", node="n1", auth_header="PVEAPIToken=t=s",
                         verify=False, transport=httpx.MockTransport(handler),
                         backoff=0, **kw)


async def test_clone_and_wait_task():
    seen = []
    polls = {"n": 0}

    def handler(req):
        seen.append((req.method, req.url.raw_path.decode(), req.content.decode()))
        assert req.headers["authorization"] == "PVEAPIToken=t=s"
        if req.method == "POST":
            return httpx.Response(200, json={"data": "UPID:n1:0001:abc:qmclone:9001:u@pve:"})
        polls["n"] += 1
        data = {"status": "running"} if polls["n"] < 3 else {"status": "stopped", "exitstatus": "OK"}
        return httpx.Response(200, json={"data": data})

    c = client(handler)
    upid = await c.clone(9001, 9100, "pipez-x", "pipez")
    assert upid.startswith("UPID:")
    method, path, body = seen[0]
    assert path == "/api2/json/nodes/n1/qemu/9001/clone"
    assert "newid=9100" in body and "full=0" in body and "pool=pipez" in body
    await c.wait_task(upid, poll=0)
    assert polls["n"] == 3
    assert "%3A" in seen[1][1]   # UPID colons are encoded in the path


async def test_wait_task_failure():
    c = client(lambda r: httpx.Response(200, json={"data": {"status": "stopped", "exitstatus": "boom"}}))
    with pytest.raises(PveError, match="boom"):
        await c.wait_task("UPID:x", poll=0)


async def test_free_vmid_skips_pool_members_and_range():
    def handler(req):
        assert req.url.path.endswith("/pools/pipez")
        return httpx.Response(200, json={"data": {"members": [
            {"vmid": 9100}, {"vmid": 9101}, {"vmid": 9001}]}})

    c = client(handler)
    taken = await c.pool_members("pipez")
    assert taken == [9100, 9101, 9001]
    assert c.free_vmid("9100-9199", taken) == 9102
    assert c.free_vmid("9100-9199", []) == 9100
    with pytest.raises(PveError):
        c.free_vmid("9100-9101", taken)


async def test_5xx_retried_then_transient():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(500)

    with pytest.raises(PveTransientError):
        await client(handler).status(9100)
    assert calls["n"] == 4   # first try + 3 retries


async def test_5xx_then_success():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500)
        return httpx.Response(200, json={"data": {"status": "running"}})

    assert (await client(handler).status(9100))["status"] == "running"


async def test_403_not_retried():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(403)

    with pytest.raises(PveError) as ei:
        await client(handler).stop(9100)
    assert not isinstance(ei.value, PveTransientError) and ei.value.status == 403
    assert calls["n"] == 1


async def test_transport_error_is_transient():
    def handler(req):
        raise httpx.ConnectError("down")

    with pytest.raises(PveTransientError):
        await client(handler).status(1)


async def test_set_config_double_encodes_sshkeys():
    seen = []

    def handler(req):
        seen.append(req.content.decode())
        return httpx.Response(200, json={"data": None})

    await client(handler).set_config(9100, sshkeys="ssh-ed25519 AAA+/= pipez",
                                     ciuser="etl", ipconfig0="ip=dhcp")
    assert "sshkeys=ssh-ed25519%2520AAA%252B%252F%253D%2520pipez" in seen[0]
    assert "ciuser=etl" in seen[0] and "ipconfig0=ip%3Ddhcp" in seen[0]


async def test_error_body_surfaced():
    def handler(req):
        return httpx.Response(400, json={"data": None, "message": "Parameter verification failed.\n",
                                         "errors": {"sshkeys": "invalid format"}})

    with pytest.raises(PveError, match="sshkeys: invalid format") as ei:
        await client(handler).set_config(9100, ciuser="etl")
    assert "Parameter verification failed." in ei.value.message


async def test_wait_task_failed_exitstatus():
    def handler(req):
        return httpx.Response(200, json={"data": {"status": "stopped", "exitstatus": "boom"}})

    with pytest.raises(PveError, match="boom"):
        await client(handler).wait_task("UPID:x", poll=0)
