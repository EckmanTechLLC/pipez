"""FastAPI server: submit runs, stream live task events (SSE), manage prompts,
serve the dev UI."""
import asyncio
import json
import logging
import os

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import config
from .db import Database
from .engine import Engine
from .events import EventBus
from .llm import make_llm
from .prefs import PreferenceStore
from . import seed_prompts

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)s %(levelname)s %(message)s")

UI_DIR = os.path.join(os.path.dirname(__file__), "..", "ui")

app = FastAPI(title="PIPEZ")
# Built at startup, never at import: importing this module must not touch the
# filesystem (tests and tooling import it). Tests may preset db/prefs/engine.
db: Database | None = None
prefs: PreferenceStore | None = None
bus = EventBus()
engine: Engine | None = None


@app.on_event("startup")
async def startup():
    global db, prefs, engine
    if db is None:
        db = Database(config.DB_PATH)
    seed_prompts.seed(db)
    if prefs is None:
        prefs = PreferenceStore(db)
    engine = Engine(db, bus, make_llm(), prefs)
    await engine.recover()      # interrupted runs -> failed (never auto-resumed)
    try:
        await engine.sweep()
    except Exception:
        logging.getLogger(__name__).exception("startup orphan sweep failed")
    global _expire_task
    _expire_task = asyncio.create_task(_expire_loop())


_expire_task = None


async def _expire_loop():
    log = logging.getLogger(__name__)
    while True:
        try:
            await engine.expire_deployments()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("deployment expiry pass failed")
        await asyncio.sleep(max(config.DEPLOY_SWEEP_SECONDS, 1))


@app.on_event("shutdown")
async def shutdown():
    if _expire_task is not None:
        _expire_task.cancel()


class SubmitRequest(BaseModel):
    request: str
    context: str | None = None
    parent_run_id: str | None = None


class IntakeAnswers(BaseModel):
    answers: dict[str, str] = {}
    reasons: dict[str, str] | None = None
    accept_all: bool = False


class PlanDecision(BaseModel):
    approve: bool
    note: str | None = None
    plan: dict | None = None


class TaskAnswer(BaseModel):
    value: str
    reason: str | None = None


class PromptBody(BaseModel):
    content: str


class ObservationBody(BaseModel):
    key: str
    value: str
    scope: str
    source: str
    project_id: str | None = None
    run_id: str | None = None
    reason: str | None = None


class PinBody(BaseModel):
    value: str


@app.post("/api/requests")
async def submit(body: SubmitRequest):
    text = body.request.strip()
    if not text:
        raise HTTPException(400, "empty request")
    context = (body.context or "").strip() or None
    parent_run_id = None
    if body.parent_run_id:
        prev = db.get_run(body.parent_run_id)
        if not prev:
            raise HTTPException(404, "parent run not found")
        if prev["status"] != "done" or not prev["result"]:
            raise HTTPException(400, "parent run has no finished result to modify")
        parent_run_id = prev["id"]
        prev_result = json.loads(prev["result"])
        basis = (
            "# Prior run being modified (its output is the baseline — apply "
            "the new request as a change to it, keeping everything not "
            "affected by the change)\n"
            "## Prior request\n%s\n\n## Prior deliverable\n%s\n\n## Prior facts\n%s"
            % (prev["request"], prev_result.get("output", ""),
               json.dumps(prev_result.get("facts", []), indent=2))
        )
        if prev.get("context"):
            basis = "# Prior run's project context\n%s\n\n%s" % (prev["context"], basis)
        context = basis + ("\n\n# Additional context\n" + context if context else "")
    run_id = engine.submit(text, context, parent_run_id)
    return {"run_id": run_id, "status": "intake"}


@app.get("/api/runs")
async def runs():
    return db.get_runs()


@app.get("/api/runs/{run_id}")
async def run_detail(run_id: str):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "no such run")
    return run


@app.get("/api/runs/{run_id}/spend")
async def run_spend(run_id: str):
    if not db.get_run(run_id):
        raise HTTPException(404, "no such run")
    return db.get_spend(run_id)


@app.get("/api/projects/{project_id}/spend")
async def project_spend(project_id: str):
    rows = db._query("SELECT id FROM runs WHERE project_id=?", (project_id,))
    if not rows:
        raise HTTPException(404, "no such project")
    return db.get_spend([r["id"] for r in rows])


def _intake_view(run: dict) -> dict:
    return {"status": run["status"], "decisions": run.get("decisions"),
            "answers": run.get("answers"), "brief": run.get("brief")}


@app.get("/api/runs/{run_id}/intake")
async def get_intake(run_id: str):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "no such run")
    return _intake_view(run)


@app.post("/api/runs/{run_id}/intake")
async def post_intake(run_id: str, body: IntakeAnswers):
    try:
        engine.answer_intake(run_id, body.answers, body.reasons, body.accept_all)
    except ValueError as e:
        raise HTTPException(404 if str(e) == "no such run" else 400, str(e))
    return _intake_view(db.get_run(run_id))


def _plan_view(run: dict) -> dict:
    return {"status": run["status"], "plan": run.get("plan"),
            "plan_meta": run.get("plan_meta"),
            "plan_revisions": run.get("plan_revisions") or 0}


@app.get("/api/runs/{run_id}/plan")
async def get_plan(run_id: str):
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "no such run")
    return _plan_view(run)


@app.post("/api/runs/{run_id}/plan")
async def post_plan(run_id: str, body: PlanDecision):
    try:
        engine.answer_plan(run_id, body.approve, body.note, body.plan)
    except ValueError as e:
        raise HTTPException(404 if str(e) == "no such run" else 400, str(e))
    return _plan_view(db.get_run(run_id))


@app.get("/api/runs/{run_id}/sandbox")
async def run_sandbox(run_id: str):
    if not db.get_run(run_id):
        raise HTTPException(404, "no such run")
    return db.get_sandbox(run_id)


@app.get("/api/runs/{run_id}/deployment")
async def run_deployment(run_id: str):
    if not db.get_run(run_id):
        raise HTTPException(404, "no such run")
    return db.get_deployment(run_id)


@app.get("/api/deployments")
async def deployments():
    return db.list_deployments()


class KeepBody(BaseModel):
    keep: bool


@app.post("/api/deployments/expire")
async def expire_deployments():
    return await engine.expire_deployments()


@app.put("/api/runs/{run_id}/deployment/keep")
async def keep_deployment(run_id: str, body: KeepBody):
    try:
        return engine.set_deploy_keep(run_id, body.keep)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/runs/{run_id}/deploy")
async def deploy_run(run_id: str):
    try:
        engine.deploy(run_id)
    except ValueError as e:
        raise HTTPException(404 if str(e) == "no such run" else 400, str(e))
    return db.get_deployment(run_id) or {"run_id": run_id, "status": "provisioning"}


@app.post("/api/runs/{run_id}/undeploy")
async def undeploy_run(run_id: str):
    try:
        return await engine.undeploy(run_id)
    except ValueError as e:
        raise HTTPException(404 if str(e) in ("no such run", "no such deployment") else 400, str(e))


@app.get("/api/sandboxes")
async def sandboxes():
    return db.list_sandboxes()


@app.post("/api/sandboxes/sweep")
async def sweep_sandboxes():
    return await engine.sweep()


@app.post("/api/sandboxes/{run_id}/destroy")
async def destroy_sandbox(run_id: str):
    try:
        return await engine.destroy_sandbox(run_id)
    except ValueError as e:
        raise HTTPException(404 if str(e) == "no such sandbox" else 400, str(e))


@app.get("/api/runs/{run_id}/tasks")
async def run_tasks(run_id: str):
    if not db.get_run(run_id):
        raise HTTPException(404, "no such run")
    return db.get_tasks(run_id)


@app.get("/api/runs/{run_id}/artifacts")
async def run_artifacts(run_id: str):
    return db.get_artifacts(run_id)


@app.post("/api/runs/{run_id}/render")
async def rerender(run_id: str):
    try:
        return await engine.rerender(run_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/runs/{run_id}/resume")
async def resume(run_id: str):
    try:
        to_redo = engine.resume(run_id)
        return {"resumed": True, "tasks_to_redo": to_redo}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/runs/{run_id}/stop")
async def stop(run_id: str):
    try:
        return await engine.stop(run_id)
    except ValueError as e:
        raise HTTPException(404 if str(e) == "no such run" else 400, str(e))


@app.post("/api/runs/{run_id}/tasks/{task_id}/answer")
async def answer_task(run_id: str, task_id: str, body: TaskAnswer):
    try:
        return engine.answer_task(run_id, task_id, body.value, body.reason)
    except ValueError as e:
        raise HTTPException(404 if str(e) == "no such task" else 400, str(e))


@app.get("/api/runs/{run_id}/verifications")
async def run_verifications(run_id: str):
    return db.get_verifications(run_id)


@app.post("/api/runs/{run_id}/verify")
async def verify(run_id: str):
    run = db.get_run(run_id)
    if run and run.get("plan"):         # acceptance review of the live deployment
        try:
            engine.reverify(run_id)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"started": True}
    if not run or run["status"] != "done" or not run["result"]:
        raise HTTPException(400, "run has no finished result to verify")
    asyncio.get_running_loop().create_task(engine.verify(run_id))
    return {"started": True}


@app.api_route("/artifacts/{run_id}/{filename}", methods=["GET", "HEAD"])
async def artifact_file(run_id: str, filename: str):
    safe = os.path.basename(filename)
    path = os.path.abspath(os.path.join(config.ARTIFACTS_DIR, run_id, safe))
    if not path.startswith(os.path.abspath(config.ARTIFACTS_DIR)) or not os.path.isfile(path):
        raise HTTPException(404, "no such artifact")
    return FileResponse(path, filename=safe)


@app.get("/api/tasks/{task_id}/log")
async def task_log(task_id: str):
    return db.get_log(task_id)


@app.post("/api/observations")
async def add_observation(body: ObservationBody):
    try:
        return prefs.observe(body.key, body.value, body.scope, body.source,
                             body.project_id, body.run_id, body.reason)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/observations")
async def list_observations(key: str | None = None, project_id: str | None = None,
                            limit: int = 100):
    return prefs.observations(key, project_id, limit)


@app.get("/api/preferences")
async def list_preferences(project_id: str | None = None):
    return prefs.resolve_all(project_id)


@app.get("/api/preferences/{key}")
async def get_preference(key: str, project_id: str | None = None):
    return prefs.resolve(key, project_id)


@app.put("/api/preferences/{key}")
async def pin_preference(key: str, body: PinBody):
    return prefs.pin(key, body.value)


@app.delete("/api/preferences/{key}")
async def retire_preference(key: str):
    return prefs.retire(key)


@app.get("/api/prompts")
async def prompts():
    return db.get_prompts()


@app.put("/api/prompts/{name}")
async def set_prompt(name: str, body: PromptBody):
    db.set_prompt(name, body.content)
    return {"ok": True}


@app.get("/api/events")
async def events():
    """SSE stream: a snapshot of recent runs/tasks, then every state change."""
    q = bus.subscribe()

    async def stream():
        try:
            snapshot_runs = db.get_runs(limit=10)
            snapshot = {
                "type": "snapshot",
                "runs": snapshot_runs,
                "tasks": {r["id"]: db.get_tasks(r["id"]) for r in snapshot_runs},
                "artifacts": {r["id"]: db.get_artifacts(r["id"]) for r in snapshot_runs},
                "intake": {r["id"]: _intake_view(r) for r in snapshot_runs},
                "plan": {r["id"]: _plan_view(r) for r in snapshot_runs},
                "sandbox": {r["id"]: db.get_sandbox(r["id"]) for r in snapshot_runs},
                "deployment": {r["id"]: db.get_deployment(r["id"]) for r in snapshot_runs},
                "verifications": {r["id"]: db.get_verifications(r["id"])
                                  for r in snapshot_runs},
            }
            yield "data: %s\n\n" % json.dumps(snapshot)
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15)
                    yield "data: %s\n\n" % json.dumps(event)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/")
async def index():
    return FileResponse(os.path.join(UI_DIR, "index.html"))
