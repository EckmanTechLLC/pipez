from pipez import seed_prompts
from pipez.engine import Engine


def _warn(tasks):
    return Engine._plan_rule_warnings(Engine, {"tasks": tasks})


def test_prompt_has_rules():
    text = str(seed_prompts.DEFAULTS["plan_architect"])
    assert "EXECUTION BOUNDARY" in text and "ACCEPTANCE CRITERIA" in text


def test_deployment_task_warning():
    bad = {"id": "t1", "title": "Live deploy to the real Ubuntu VM",
           "prompt": "Deploy and curl /healthz", "acceptance": []}
    ok = {"id": "t2", "title": "Write the systemd unit deploy/pipez.service",
          "prompt": "Author the unit file deploy/pipez.service.", "acceptance": []}
    w = _warn([bad, ok])
    assert len(w) == 1 and w[0].startswith("task_touches_deployment") and "t1" in w[0]


def test_process_criteria_warning():
    t = {"id": "t1", "title": "x", "prompt": "y", "acceptance": [
        "The report gives the npm ci rc",
        'POST /shorten with javascript: returns 400 with {"error":"invalid_url"}']}
    w = _warn([t])
    assert len(w) == 1 and w[0].startswith("process_acceptance_criteria")
    assert "npm ci rc" in w[0]


def test_deployment_heuristic_cases():
    pos = ["Live deploy to the real Ubuntu VM and report the /healthz result",
           "Verification gate and live redeploy to the VM"]
    neg = ["Write the systemd unit deploy/pipez.service",
           "Add static node:test checks for deploy files",
           "README: Deploy to an Ubuntu VM (systemd) section",
           "Local verification gate",
           "Local verification gate: build, tests, lint the unit and scripts"]
    for i, ti in enumerate(pos):
        assert len(_warn([{"id": "p%d" % i, "title": ti, "prompt": ""}])) == 1, ti
    for i, ti in enumerate(neg):
        assert _warn([{"id": "n%d" % i, "title": ti, "prompt": ""}]) == [], ti
