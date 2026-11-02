"""Versioned research tasks. Review records are local operator assertions, not clinical validation."""
from __future__ import annotations

from .pubmed import bounded_text, digest, canonical
from .service import now, record_id
import uuid


class TaskService:
    def __init__(self, research):
        self.research = research

    def get(self, task_id):
        task = self.research._load("task", record_id(task_id))
        if digest(task["snapshot"]) != task["digest"]:
            raise ValueError("Task integrity mismatch")
        return task

    def _save(self, task_id, snapshot, previous=None):
        revision = snapshot["revision"]
        task = {"task_id": task_id, "snapshot": snapshot, "digest": digest(snapshot),
                "previous_digest": previous, "scientifically_verified": None}
        encoded = canonical(task)
        if len(encoded.encode()) > 1_500_000:
            raise ValueError("Task too large")
        db = self.research.db
        with db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT body FROM records WHERE kind='task' AND id=?", (task_id,)).fetchone()
            if previous is not None:
                from .service import parse_json
                if current is None or parse_json(current[0])["digest"] != previous:
                    raise ValueError("Concurrent task change; reload before retrying")
            elif current is not None:
                raise ValueError("Task already exists")
            db.execute("INSERT INTO records VALUES (?,?,?)", ("task_revision", f"{task_id}:{revision}", encoded))
            db.execute("INSERT OR REPLACE INTO records VALUES (?,?,?)", ("task", task_id, encoded))
        return task

    def create(self, question, inclusion, exclusion):
        criteria = self._criteria(inclusion, exclusion)
        return self._save(uuid.uuid4().hex, {"question": bounded_text(question, "question"),
            **criteria, "revision": 1, "stage": "draft", "bundle_ids": [], "screening": [],
            "review": None, "updated_at": now()})

    @staticmethod
    def _criteria(inclusion, exclusion):
        for label, values in (("inclusion", inclusion), ("exclusion", exclusion)):
            if not isinstance(values, list) or not 1 <= len(values) <= 20:
                raise ValueError(f"Expected 1..20 explicit {label} criteria")
        return {"inclusion": [bounded_text(v, "criterion") for v in inclusion],
                "exclusion": [bounded_text(v, "criterion") for v in exclusion]}

    def update(self, task_id, expected_digest, question, inclusion, exclusion):
        task = self._current(task_id, expected_digest)
        snapshot = {**task["snapshot"], **self._criteria(inclusion, exclusion),
                    "question": bounded_text(question, "question"), "screening": []}
        # Changing scope also clears selections: old evidence must be reconsidered.
        snapshot["bundle_ids"] = []
        return self._revise(task, snapshot)

    def _current(self, task_id, expected_digest):
        task = self.get(task_id)
        if task["digest"] != expected_digest:
            raise ValueError("Task changed; reload before editing or reviewing")
        return task

    def _revise(self, task, snapshot):
        snapshot = {**snapshot, "revision": task["snapshot"]["revision"] + 1,
                    "stage": "draft", "review": None, "updated_at": now()}
        return self._save(task["task_id"], snapshot, task["digest"])

    def screen(self, task_id, expected_digest, pmid, decision, reason):
        from .pubmed import pmid as validate_pmid
        task = self._current(task_id, expected_digest)
        identifier = validate_pmid(pmid)
        if decision not in {"include", "exclude", "uncertain"}:
            raise ValueError("Invalid screening decision")
        # Require an actually retrieved article, not a guessed identifier.
        source = self.research.article(identifier)
        row = {"pmid": identifier, "source_sha256": source["content_sha256"],
               "decision": decision, "reason": bounded_text(reason, "screening reason")}
        rows = [r for r in task["snapshot"]["screening"] if r["pmid"] != identifier]
        if len(rows) >= 100:
            raise ValueError("Task screening limit reached")
        return self._revise(task, {**task["snapshot"], "screening": rows + [row]})

    def attach(self, task_id, expected_digest, bundle_id):
        task = self._current(task_id, expected_digest)
        self.research.audit_bundle(bundle_id)
        ids = list(dict.fromkeys([*task["snapshot"]["bundle_ids"], bundle_id]))
        if len(ids) > 20:
            raise ValueError("Task bundle limit reached")
        return self._revise(task, {**task["snapshot"], "bundle_ids": ids})

    def request_review(self, task_id, expected_digest):
        task = self._current(task_id, expected_digest)
        s = task["snapshot"]
        if not s["bundle_ids"] or not any(r["decision"] == "include" for r in s["screening"]):
            raise ValueError("Review needs evidence and at least one included source")
        included = {(r["pmid"], r["source_sha256"]) for r in s["screening"] if r["decision"] == "include"}
        for identifier in s["bundle_ids"]:
            self.research.audit_bundle(identifier)
            bundle = self.research._load("bundle", identifier)
            if any((c["pmid"], c["source_sha256"]) not in included for c in bundle["manifest"]["claims"]):
                raise ValueError("Every cited source snapshot must have an include decision")
        snapshot = {**s, "revision": s["revision"] + 1, "stage": "pending_human_review",
                    "review": None, "updated_at": now()}
        return self._save(task_id, snapshot, task["digest"])

    def record_operator_review(self, task_id, expected_digest, reviewer, decision, note):
        # This operation is deliberately NOT exposed as an MCP tool.
        task = self._current(task_id, expected_digest)
        if task["snapshot"]["stage"] != "pending_human_review":
            raise ValueError("Task is not pending review")
        if decision not in {"approve", "reject"}:
            raise ValueError("Invalid review decision")
        review = {"reviewer": bounded_text(reviewer, "reviewer", 200), "decision": decision,
                  "note": bounded_text(note, "review note"), "reviewed_digest": expected_digest,
                  "at": now(), "identity_verified": False}
        return self._save(task_id, {**task["snapshot"], "revision": task["snapshot"]["revision"] + 1,
            "stage": "review_recorded", "review": review, "updated_at": now()}, expected_digest)
