"""Preference store: observations (one human decision each) and per-key
resolution with strict precedence. Storage + rules only; no LLM calls.

Precedence: request value > pinned > project's latest answer > learned
global default > none (the intake must ask).
"""
import time
from collections import Counter

from . import config

SCOPES = ("request", "project", "global")
SOURCES = ("intake", "request", "override")


class PreferenceStore:
    def __init__(self, db):
        self.db = db

    # -- writes -------------------------------------------------------------

    def observe(self, key, value, scope, source, project_id=None, run_id=None,
                reason=None) -> dict:
        if scope not in SCOPES:
            raise ValueError("scope must be one of %s" % (SCOPES,))
        if source not in SOURCES:
            raise ValueError("source must be one of %s" % (SOURCES,))
        cur = self.db._exec(
            "INSERT INTO observations (key, value, scope, project_id, run_id, "
            "source, reason, ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (key, value, scope, project_id, run_id, source, reason, time.time()),
        )
        return self.db._query("SELECT * FROM observations WHERE id=?",
                              (cur.lastrowid,))[0]

    def pin(self, key, value):
        self.db._exec(
            "INSERT INTO preferences (key, status, value, updated_at, retired_at) "
            "VALUES (?, 'pinned', ?, ?, NULL) "
            "ON CONFLICT(key) DO UPDATE SET status='pinned', value=excluded.value, "
            "updated_at=excluded.updated_at, retired_at=NULL",
            (key, value, time.time()),
        )
        return self._pref_row(key)

    def retire(self, key):
        now = time.time()
        self.db._exec(
            "INSERT INTO preferences (key, status, value, updated_at, retired_at) "
            "VALUES (?, 'retired', NULL, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET status='retired', value=NULL, "
            "updated_at=excluded.updated_at, retired_at=excluded.retired_at",
            (key, now, now),
        )
        return self._pref_row(key)

    # -- reads --------------------------------------------------------------

    def _pref_row(self, key):
        rows = self.db._query("SELECT * FROM preferences WHERE key=?", (key,))
        return rows[0] if rows else None

    def observations(self, key=None, project_id=None, limit=100):
        where, params = [], []
        if key is not None:
            where.append("key=?")
            params.append(key)
        if project_id is not None:
            where.append("project_id=?")
            params.append(project_id)
        q = "SELECT * FROM observations"
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY ts DESC, id DESC LIMIT ?"
        return self.db._query(q, tuple(params) + (limit,))

    @staticmethod
    def _is_exception(o):
        return o["source"] == "override" and bool((o["reason"] or "").strip())

    def _evidence(self, key):
        rows = self.db._query(
            "SELECT * FROM observations WHERE key=? ORDER BY ts, id", (key,))
        return {
            "count": len(rows),
            "last_seen": rows[-1]["ts"] if rows else None,
            "projects": sorted({r["project_id"] for r in rows if r["project_id"]}),
            "exceptions": [
                {"value": r["value"], "reason": r["reason"],
                 "project_id": r["project_id"], "ts": r["ts"]}
                for r in rows if self._is_exception(r)
            ],
        }

    def learned_default(self, key):
        """Graduation rule. Looks at the latest PREF_WINDOW non-exception,
        non-request-scope observations; a value wins if it has at least
        PREF_GRADUATE votes and is the strict plurality (ties for the top
        count do not graduate). Suppressed after retire() until a newer
        observation arrives. Returns {value, count, window} or None."""
        pref = self._pref_row(key)
        rows = self.db._query(
            "SELECT * FROM observations WHERE key=? AND scope != 'request' "
            "ORDER BY ts DESC, id DESC", (key,))
        if pref and pref["status"] == "retired":
            if not any(r["ts"] > pref["retired_at"] for r in rows):
                return None
        window = [r for r in rows if not self._is_exception(r)][:config.PREF_WINDOW]
        if not window:
            return None
        ranked = Counter(r["value"] for r in window).most_common()
        value, n = ranked[0]
        if n < config.PREF_GRADUATE or (len(ranked) > 1 and ranked[1][1] == n):
            return None
        return {"value": value, "count": n, "window": len(window)}

    def resolve(self, key, project_id=None, request_values=None) -> dict:
        evidence = self._evidence(key)
        out = None
        if request_values and key in request_values:
            out = {"value": request_values[key], "source": "request"}
        else:
            pref = self._pref_row(key)
            if pref and pref["status"] == "pinned":
                out = {"value": pref["value"], "source": "pinned"}
            elif project_id:
                rows = self.db._query(
                    "SELECT value FROM observations WHERE key=? AND project_id=? "
                    "AND scope != 'request' ORDER BY ts DESC, id DESC LIMIT 1",
                    (key, project_id))
                if rows:
                    out = {"value": rows[0]["value"], "source": "project"}
            if out is None:
                learned = self.learned_default(key)
                if learned:
                    out = {"value": learned["value"], "source": "global"}
                    evidence = dict(evidence, count=learned["count"])
        if out is None:
            out = {"value": None, "source": "none"}
        out["evidence"] = evidence
        return out

    def resolve_all(self, project_id=None, request_values=None) -> dict:
        keys = {r["key"] for r in self.db._query(
            "SELECT DISTINCT key FROM observations")}
        keys |= {r["key"] for r in self.db._query("SELECT key FROM preferences")}
        return {k: self.resolve(k, project_id, request_values)
                for k in sorted(keys)}
