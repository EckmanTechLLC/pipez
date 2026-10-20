"""SQLite persistence: runs, tasks, task_log, prompts.

The task tree is the system of record — every state transition lands here
before it is published to the UI. WAL mode so the dev UI's reads never block
engine writes.
"""
import json
import os
import sqlite3
import threading
import time
import uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    request     TEXT NOT NULL,
    context     TEXT,
    status      TEXT NOT NULL DEFAULT 'running',
    result      TEXT,
    error       TEXT,
    created_at  REAL NOT NULL,
    finished_at REAL
);
CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES runs(id),
    parent_id     TEXT REFERENCES tasks(id),
    depth         INTEGER NOT NULL DEFAULT 0,
    seq           INTEGER NOT NULL DEFAULT 0,
    role          TEXT NOT NULL,
    title         TEXT NOT NULL,
    prompt        TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    action        TEXT,
    result        TEXT,
    error         TEXT,
    model         TEXT,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    started_at    REAL,
    finished_at   REAL,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_run ON tasks(run_id);
CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id);
CREATE TABLE IF NOT EXISTS task_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    event   TEXT NOT NULL,
    detail  TEXT,
    ts      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_log_task ON task_log(task_id);
CREATE TABLE IF NOT EXISTS prompts (
    name       TEXT PRIMARY KEY,
    content    TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""

TASK_COLUMNS = [
    "id", "run_id", "parent_id", "depth", "seq", "role", "title", "prompt",
    "context", "tier", "status", "action", "result", "error", "model",
    "input_tokens", "output_tokens", "started_at", "finished_at",
    "created_at", "updated_at", "effort", "depends_on",
    "kind", "module", "acceptance", "cost_usd", "question",
]

MIGRATIONS = [
    "ALTER TABLE tasks ADD COLUMN context TEXT",
    "ALTER TABLE runs ADD COLUMN context TEXT",
    "ALTER TABLE runs ADD COLUMN parent_run_id TEXT",
    "ALTER TABLE tasks ADD COLUMN tier TEXT",
    "ALTER TABLE tasks ADD COLUMN effort TEXT",
    "ALTER TABLE tasks ADD COLUMN depends_on TEXT",
    """CREATE TABLE IF NOT EXISTS artifacts (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id     TEXT NOT NULL,
        filename   TEXT NOT NULL,
        path       TEXT NOT NULL,
        bytes      INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS verifications (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id     TEXT NOT NULL,
        task_id    TEXT NOT NULL,
        verdicts   TEXT NOT NULL,
        summary    TEXT,
        created_at REAL NOT NULL
    )""",
    "ALTER TABLE runs ADD COLUMN project_id TEXT",
    """CREATE TABLE IF NOT EXISTS observations (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        key        TEXT NOT NULL,
        value      TEXT NOT NULL,
        scope      TEXT NOT NULL,
        project_id TEXT,
        run_id     TEXT,
        source     TEXT NOT NULL,
        reason     TEXT,
        ts         REAL NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_obs_key_ts ON observations(key, ts)",
    "ALTER TABLE runs ADD COLUMN decisions TEXT",
    "ALTER TABLE runs ADD COLUMN answers TEXT",
    "ALTER TABLE runs ADD COLUMN brief TEXT",
    "ALTER TABLE runs ADD COLUMN intake_meta TEXT",
    "ALTER TABLE runs ADD COLUMN plan TEXT",
    "ALTER TABLE runs ADD COLUMN plan_meta TEXT",
    "ALTER TABLE runs ADD COLUMN plan_revisions INTEGER DEFAULT 0",
    "ALTER TABLE tasks ADD COLUMN kind TEXT",
    "ALTER TABLE tasks ADD COLUMN module TEXT",
    "ALTER TABLE tasks ADD COLUMN acceptance TEXT",
    "ALTER TABLE tasks ADD COLUMN cost_usd REAL",
    "ALTER TABLE tasks ADD COLUMN question TEXT",
    """CREATE TABLE IF NOT EXISTS sandboxes (
        run_id       TEXT PRIMARY KEY,
        kind         TEXT,
        vmid         INTEGER,
        name         TEXT,
        ip           TEXT,
        status       TEXT,
        created_at   REAL,
        ready_at     REAL,
        destroyed_at REAL,
        error        TEXT,
        kept         INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS deployments (
        run_id       TEXT PRIMARY KEY,
        vmid         INTEGER,
        name         TEXT,
        ip           TEXT,
        url          TEXT,
        status       TEXT,
        created_at   REAL,
        ready_at     REAL,
        destroyed_at REAL,
        error        TEXT,
        log          TEXT
    )""",
    "ALTER TABLE runs ADD COLUMN verification TEXT",
    "ALTER TABLE verifications ADD COLUMN round INTEGER",
    """CREATE TABLE IF NOT EXISTS preferences (
        key        TEXT PRIMARY KEY,
        status     TEXT NOT NULL,
        value      TEXT,
        updated_at REAL NOT NULL,
        retired_at REAL
    )""",
    "ALTER TABLE runs ADD COLUMN cost_usd REAL NOT NULL DEFAULT 0",
    "ALTER TABLE deployments ADD COLUMN keep INTEGER DEFAULT 0",
    "ALTER TABLE deployments ADD COLUMN expires_at REAL",
    """CREATE TABLE IF NOT EXISTS spend (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id        TEXT NOT NULL,
        task_id       TEXT,
        stage         TEXT NOT NULL,
        model         TEXT,
        input_tokens  INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        cost_usd      REAL NOT NULL DEFAULT 0,
        source        TEXT NOT NULL DEFAULT 'estimate',
        created_at    REAL NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_spend_run ON spend(run_id)",
]


RUN_JSON_COLUMNS = ("decisions", "answers", "intake_meta", "plan", "plan_meta")


def _decode_run(row: dict) -> dict:
    for col in RUN_JSON_COLUMNS:
        if row.get(col):
            try:
                row[col] = json.loads(row[col])
            except (TypeError, ValueError):
                pass
    return row


def _decode_task(row: dict) -> dict:
    # depends_on: JSON list of sibling task ids; task status may be "skipped".
    raw = row.get("depends_on")
    try:
        row["depends_on"] = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        row["depends_on"] = []
    q = row.get("question")
    if q:
        try:
            row["question"] = json.loads(q)
        except (TypeError, ValueError):
            row["question"] = None
    acc = row.get("acceptance")
    if acc:
        try:
            row["acceptance"] = json.loads(acc)
        except (TypeError, ValueError):
            pass
    return row


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class Database:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            self._conn.executescript(SCHEMA)
            for migration in MIGRATIONS:
                try:
                    self._conn.execute(migration)
                except sqlite3.OperationalError:
                    pass  # already applied
            self._backfill_project_ids()
            self._conn.commit()

    def _backfill_project_ids(self):
        """Runs created before project_id existed: roots get their own id,
        children take the root of their parent_run_id chain."""
        rows = self._conn.execute(
            "SELECT id, parent_run_id, project_id FROM runs").fetchall()
        parents = {r["id"]: r["parent_run_id"] for r in rows}
        for r in rows:
            if r["project_id"]:
                continue
            root, seen = r["id"], set()
            while parents.get(root) in parents and root not in seen:
                seen.add(root)
                root = parents[root]
            self._conn.execute("UPDATE runs SET project_id=? WHERE id=?",
                               (root, r["id"]))

    def _exec(self, query, params=()):
        with self._lock:
            cur = self._conn.execute(query, params)
            self._conn.commit()
            return cur

    def _query(self, query, params=()):
        with self._lock:
            return [dict(r) for r in self._conn.execute(query, params).fetchall()]

    # -- sandboxes ----------------------------------------------------------

    SANDBOX_FIELDS = ("kind", "vmid", "name", "ip", "status", "created_at",
                      "ready_at", "destroyed_at", "error", "kept")

    def upsert_sandbox(self, run_id, **fields):
        bad = set(fields) - set(self.SANDBOX_FIELDS)
        if bad:
            raise ValueError("unknown sandbox fields: %s" % sorted(bad))
        if self.get_sandbox(run_id) is None:
            fields.setdefault("created_at", time.time())
            cols = ["run_id"] + list(fields)
            self._exec("INSERT INTO sandboxes (%s) VALUES (%s)" % (
                ", ".join(cols), ", ".join("?" * len(cols))),
                [run_id] + list(fields.values()))
        elif fields:
            self._exec("UPDATE sandboxes SET %s WHERE run_id=?" % ", ".join(
                "%s=?" % k for k in fields), list(fields.values()) + [run_id])
        return self.get_sandbox(run_id)

    def get_sandbox(self, run_id):
        rows = self._query("SELECT * FROM sandboxes WHERE run_id=?", (run_id,))
        return rows[0] if rows else None

    def list_sandboxes(self, status=None):
        if status:
            return self._query("SELECT * FROM sandboxes WHERE status=? "
                               "ORDER BY created_at DESC", (status,))
        return self._query("SELECT * FROM sandboxes ORDER BY created_at DESC")

    # -- deployments --------------------------------------------------------

    DEPLOYMENT_FIELDS = ("vmid", "name", "ip", "url", "status", "created_at",
                         "ready_at", "destroyed_at", "error", "log", "keep",
                         "expires_at")

    def upsert_deployment(self, run_id, **fields):
        bad = set(fields) - set(self.DEPLOYMENT_FIELDS)
        if bad:
            raise ValueError("unknown deployment fields: %s" % sorted(bad))
        if self.get_deployment(run_id) is None:
            fields.setdefault("created_at", time.time())
            cols = ["run_id"] + list(fields)
            self._exec("INSERT INTO deployments (%s) VALUES (%s)" % (
                ", ".join(cols), ", ".join("?" * len(cols))),
                [run_id] + list(fields.values()))
        elif fields:
            self._exec("UPDATE deployments SET %s WHERE run_id=?" % ", ".join(
                "%s=?" % k for k in fields), list(fields.values()) + [run_id])
        return self.get_deployment(run_id)

    def replace_deployment(self, run_id):
        """Forget a previous (failed/destroyed) deployment row before a new one."""
        self._exec("DELETE FROM deployments WHERE run_id=?", (run_id,))

    def get_deployment(self, run_id):
        rows = self._query("SELECT * FROM deployments WHERE run_id=?", (run_id,))
        return rows[0] if rows else None

    def list_deployments(self):
        return self._query("SELECT * FROM deployments ORDER BY created_at DESC")

    # -- runs ---------------------------------------------------------------

    def create_run(self, request: str, context: str = None,
                   parent_run_id: str = None, status: str = "running") -> str:
        run_id = new_id()
        project_id = run_id
        if parent_run_id:
            parent = self.get_run(parent_run_id)
            if parent and parent.get("project_id"):
                project_id = parent["project_id"]
            elif parent:
                project_id = parent["id"]
        self._exec(
            "INSERT INTO runs (id, request, context, parent_run_id, project_id, "
            "status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, request, context, parent_run_id, project_id, status,
             time.time()),
        )
        return run_id

    def set_run_status(self, run_id, status, clear_error=False):
        if clear_error:
            self._exec("UPDATE runs SET status=?, error=NULL WHERE id=?",
                       (status, run_id))
        else:
            self._exec("UPDATE runs SET status=? WHERE id=?", (status, run_id))

    def set_intake(self, run_id, decisions, meta):
        """Store discovered decisions; resets answers and brief (intake is
        always re-run from scratch)."""
        self._exec(
            "UPDATE runs SET decisions=?, answers=NULL, brief=NULL, "
            "intake_meta=? WHERE id=?",
            (json.dumps(decisions), json.dumps({"discovery": meta}), run_id))

    def set_decisions(self, run_id, decisions):
        """Replace the stored decisions only (answers/brief untouched)."""
        self._exec("UPDATE runs SET decisions=? WHERE id=?",
                   (json.dumps(decisions), run_id))

    def set_plan(self, run_id, plan, meta, revisions=None):
        """Store the plan (None clears it) and its meta; optionally the
        revision counter."""
        sql = "UPDATE runs SET plan=?, plan_meta=?"
        params = [json.dumps(plan) if plan is not None else None,
                  json.dumps(meta) if meta is not None else None]
        if revisions is not None:
            sql += ", plan_revisions=?"
            params.append(revisions)
        self._exec(sql + " WHERE id=?", (*params, run_id))

    def set_answers(self, run_id, answers):
        self._exec("UPDATE runs SET answers=? WHERE id=?",
                   (json.dumps(answers), run_id))

    def set_brief(self, run_id, brief, meta):
        run = self.get_run(run_id)
        merged = dict(run.get("intake_meta") or {}, brief=meta)
        self._exec("UPDATE runs SET brief=?, intake_meta=? WHERE id=?",
                   (brief, json.dumps(merged), run_id))

    # -- artifacts ----------------------------------------------------------

    def add_artifact(self, run_id, filename, path, nbytes):
        self._exec(
            "INSERT INTO artifacts (run_id, filename, path, bytes, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, filename, path, nbytes, time.time()),
        )

    def get_artifacts(self, run_id):
        return self._query(
            "SELECT * FROM artifacts WHERE run_id=? ORDER BY filename", (run_id,))

    # -- verifications ------------------------------------------------------

    def clear_verifications(self, run_id):
        self._exec("DELETE FROM verifications WHERE run_id=?", (run_id,))

    def add_verification(self, run_id, task_id, verdicts, summary, round=1):
        self._exec(
            "INSERT INTO verifications (run_id, task_id, verdicts, summary, created_at, round) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, task_id, json.dumps(verdicts), summary, time.time(), round),
        )

    def get_verifications(self, run_id):
        return self._query(
            "SELECT * FROM verifications WHERE run_id=? ORDER BY id", (run_id,))

    def set_run_verification(self, run_id, verification):
        """passed | failed | skipped | error (None clears it)."""
        self._exec("UPDATE runs SET verification=? WHERE id=?", (verification, run_id))

    def finish_run(self, run_id, status, result=None, error=None):
        self._exec(
            "UPDATE runs SET status=?, result=?, error=?, finished_at=? WHERE id=?",
            (status, json.dumps(result) if result is not None else None, error, time.time(), run_id),
        )

    def get_runs(self, limit=50):
        return [_decode_run(r) for r in self._query(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,))]

    def get_run(self, run_id):
        rows = self._query("SELECT * FROM runs WHERE id=?", (run_id,))
        return _decode_run(rows[0]) if rows else None

    def count_run_tasks(self, run_id) -> int:
        return self._query("SELECT COUNT(*) AS n FROM tasks WHERE run_id=?", (run_id,))[0]["n"]

    # -- tasks --------------------------------------------------------------

    def create_task(self, run_id, parent_id, depth, seq, role, title, prompt,
                    context=None, tier="deep", effort=None,
                    depends_on=None, kind=None, module=None,
                    acceptance=None) -> dict:
        now = time.time()
        task = {
            "id": new_id(), "run_id": run_id, "parent_id": parent_id,
            "depth": depth, "seq": seq, "role": role, "title": title,
            "prompt": prompt, "context": context, "tier": tier,
            "status": "pending", "action": None,
            "result": None, "error": None, "model": None,
            "input_tokens": 0, "output_tokens": 0,
            "started_at": None, "finished_at": None,
            "created_at": now, "updated_at": now,
            "effort": effort, "depends_on": list(depends_on or []),
            "kind": kind, "module": module, "acceptance": acceptance,
            "cost_usd": None, "question": None,
        }
        cols = ", ".join(TASK_COLUMNS)
        marks = ", ".join("?" for _ in TASK_COLUMNS)
        self._exec(f"INSERT INTO tasks ({cols}) VALUES ({marks})",
                   tuple(json.dumps(task[c]) if c in ("depends_on", "acceptance", "question")
                         and task[c] is not None else task[c]
                         for c in TASK_COLUMNS))
        return task

    def update_task(self, task_id, **fields) -> dict:
        fields["updated_at"] = time.time()
        if "depends_on" in fields and not isinstance(fields["depends_on"], str):
            fields["depends_on"] = json.dumps(list(fields["depends_on"] or []))
        if "acceptance" in fields and not isinstance(fields["acceptance"], (str, type(None))):
            fields["acceptance"] = json.dumps(fields["acceptance"])
        if "question" in fields and not isinstance(fields["question"], (str, type(None))):
            fields["question"] = json.dumps(fields["question"])
        sets = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE tasks SET {sets} WHERE id=?", (*fields.values(), task_id))
        return self.get_task(task_id)

    def add_tokens(self, task_id, input_tokens, output_tokens):
        self._exec(
            "UPDATE tasks SET input_tokens=input_tokens+?, output_tokens=output_tokens+?, updated_at=? WHERE id=?",
            (input_tokens, output_tokens, time.time(), task_id),
        )

    def add_cost(self, task_id, cost_usd):
        self._exec(
            "UPDATE tasks SET cost_usd=COALESCE(cost_usd, 0)+?, updated_at=? WHERE id=?",
            (float(cost_usd or 0), time.time(), task_id),
        )

    def add_spend(self, run_id, task_id, stage, model, input_tokens,
                  output_tokens, cost_usd, source="estimate"):
        """Append a ledger row and add its cost to runs.cost_usd."""
        cost = float(cost_usd or 0)
        self._exec(
            "INSERT INTO spend (run_id, task_id, stage, model, input_tokens, "
            "output_tokens, cost_usd, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, task_id, stage, model, int(input_tokens or 0),
             int(output_tokens or 0), cost, source, time.time()))
        self._exec("UPDATE runs SET cost_usd=COALESCE(cost_usd, 0)+? WHERE id=?",
                   (cost, run_id))

    def get_spend(self, run_ids):
        """Roll-up over the given runs: {total_usd, by_stage, by_model}."""
        if isinstance(run_ids, str):
            run_ids = [run_ids]
        out = {"total_usd": 0.0, "by_stage": {}, "by_model": {}}
        if not run_ids:
            return out
        marks = ",".join("?" * len(run_ids))
        rows = self._query(
            "SELECT stage, model, COUNT(*) AS calls, SUM(cost_usd) AS usd, "
            "SUM(input_tokens) AS i, SUM(output_tokens) AS o FROM spend "
            "WHERE run_id IN (%s) GROUP BY stage, model" % marks, tuple(run_ids))
        for r in rows:
            for key, name in (("by_stage", r["stage"]),
                              ("by_model", r["model"] or "unknown")):
                b = out[key].setdefault(name, {"usd": 0.0, "calls": 0,
                                               "input_tokens": 0, "output_tokens": 0})
                b["usd"] += r["usd"] or 0
                b["calls"] += r["calls"]
                b["input_tokens"] += r["i"] or 0
                b["output_tokens"] += r["o"] or 0
            out["total_usd"] += r["usd"] or 0
        return out

    def get_task(self, task_id):
        rows = self._query("SELECT * FROM tasks WHERE id=?", (task_id,))
        return _decode_task(rows[0]) if rows else None

    def get_tasks(self, run_id):
        return [_decode_task(r) for r in self._query(
            "SELECT * FROM tasks WHERE run_id=? ORDER BY created_at, seq", (run_id,))]

    def get_children(self, parent_id):
        return [_decode_task(r) for r in self._query(
            "SELECT * FROM tasks WHERE parent_id=? ORDER BY seq", (parent_id,))]

    # -- audit log ----------------------------------------------------------

    def log(self, task_id, event, detail=None):
        self._exec(
            "INSERT INTO task_log (task_id, event, detail, ts) VALUES (?, ?, ?, ?)",
            (task_id, event, json.dumps(detail) if detail is not None else None, time.time()),
        )

    def get_log(self, task_id):
        return self._query("SELECT * FROM task_log WHERE task_id=? ORDER BY ts", (task_id,))

    # -- prompts ------------------------------------------------------------

    def get_prompt(self, name):
        rows = self._query("SELECT content FROM prompts WHERE name=?", (name,))
        return rows[0]["content"] if rows else None

    def set_prompt(self, name, content):
        self._exec(
            "INSERT INTO prompts (name, content, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET content=excluded.content, updated_at=excluded.updated_at",
            (name, content, time.time()),
        )

    def get_prompts(self):
        return self._query(
            "SELECT * FROM prompts WHERE name NOT LIKE '\\_seed:%' ESCAPE '\\' ORDER BY name")

    def seed_prompt(self, name, content):
        """Update the prompt only if the user never edited it (current content
        still equals the last seeded version). User edits are never clobbered."""
        prev_seed = self.get_prompt("_seed:" + name)
        current = self.get_prompt(name)
        if current is None or current == prev_seed:
            self.set_prompt(name, content)
        self.set_prompt("_seed:" + name, content)
