"""Local single-writer candidate releases; no production activation or public rights grant."""

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from rag_portfolio.public_medical import canonical, sha, validate_corpus


def load(path):
    return json.loads(Path(path).read_text(encoding="utf8"))


def processing_gate(corpus, approval=None):
    validate_corpus(corpus)
    if corpus["corpus_id"] == "medical_public_v1":
        return
    expected = sorted((d["document_id"], d["source_sha256"]) for d in corpus["documents"])
    if (
        not approval
        or approval.get("schema_version") != "medical-processing-approval-v1"
        or approval.get("corpus_sha256") != corpus["corpus_sha256"]
        or approval.get("external_model_processing") is not True
        or approval.get("public_redistribution") is not False
        or approval.get("clinical_approval") is not False
        or approval.get("authority") != "human_user_current_task"
        or sorted((d["document_id"], d["source_sha256"]) for d in approval.get("documents", []))
        != expected
        or approval.get("approval_sha256")
        != sha(canonical({k: v for k, v in approval.items() if k != "approval_sha256"}))
    ):
        raise ValueError("Combined corpus requires pinned user processing approval")


def combined_boundary(query):
    # Same personal-data boundary as the old service; Chinese guidelines now exist in this scope.
    if ("患者" in query or "我" in query) and any(
        s in query for s in ["没有", "未提供", "缺少", "个人", "病历", "填写", "个体处方"]
    ):
        return "needs_patient_data_and_clinical_review"
    return "retrieve"


class ReleaseRegistry:
    """Atomic local file transitions. One CLI writer; serving workers only read snapshots."""

    def __init__(self, path):
        self.path = Path(path)

    def read(self):
        if not self.path.exists():
            return {
                "schema_version": "medical-release-registry-v1",
                "generation": 0,
                "active": None,
                "releases": {},
                "history": [],
            }
        state = load(self.path)
        if state["schema_version"] != "medical-release-registry-v1":
            raise ValueError("Unknown release registry")
        return state

    def _save(self, state, action, release_id):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(".lock")
        temporary = self.path.with_suffix("." + uuid4().hex + ".tmp")
        lock_stream = lock.open("xb")
        try:
            with lock_stream:
                if self.read()["generation"] != state["generation"]:
                    raise ValueError("Concurrent release transition; reload before retry")
                state["generation"] += 1
                state["history"].append(
                    {
                        "generation": state["generation"],
                        "action": action,
                        "release_id": release_id,
                        "at": datetime.now(UTC).isoformat(),
                    }
                )
                with temporary.open("xb") as stream:
                    stream.write(canonical(state) + b"\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
            lock.unlink()

    def register(self, release_id, corpus_path, index_path, approval_path=None):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", release_id):
            raise ValueError("Invalid release ID")
        state = self.read()
        if release_id in state["releases"]:
            raise ValueError("Immutable release ID already exists")
        entry = {
            "release_id": release_id,
            "corpus_path": str(Path(corpus_path).resolve()),
            "index_path": str(Path(index_path).resolve()),
            "approval_path": str(Path(approval_path).resolve()) if approval_path else None,
            "status": "candidate",
        }
        corpus, index, approval = self.bundle(entry)
        entry.update(
            corpus_sha256=corpus["corpus_sha256"],
            index_sha256=index["index_sha256"],
            corpus_version=corpus["release"],
            approval_sha256=approval["approval_sha256"] if approval else None,
        )
        entry["work_versions"] = [
            {
                "work_id": d["work_id"],
                "document_id": d["document_id"],
                "edition": d.get("edition"),
                "publication_year": d.get("publication_year"),
                "source_sha256": d["source_sha256"],
            }
            for d in corpus["documents"]
        ]
        state["releases"][release_id] = entry
        self._save(state, "register", release_id)

    def bundle(self, entry):
        from rag_portfolio.public_medical_retrieval import validate_index

        corpus, index = load(entry["corpus_path"]), load(entry["index_path"])
        approval = load(entry["approval_path"]) if entry["approval_path"] else None
        processing_gate(corpus, approval)
        validate_index(corpus, index)
        for key, value in [
            ("corpus_sha256", corpus["corpus_sha256"]),
            ("index_sha256", index["index_sha256"]),
            ("approval_sha256", approval["approval_sha256"] if approval else None),
        ]:
            if key in entry and entry[key] != value:
                raise ValueError("Registered release files changed")
        return corpus, index, approval

    def transition(self, release_id, action):
        state = self.read()
        entry = state["releases"][release_id]
        if action == "revoke":
            entry["status"] = "revoked"
            if state["active"] == release_id:
                state["active"] = None
        elif action == "validate":
            if entry["status"] == "revoked":
                raise ValueError("Revoked release cannot be validated")
            self.bundle(entry)
            entry["status"] = "validated"
        elif action in {"activate", "rollback"}:
            if entry["status"] != "validated":
                raise ValueError("Only validated releases may serve")
            self.bundle(entry)
            if action == "rollback" and not any(
                h["release_id"] == release_id and h["action"] in {"activate", "rollback"}
                for h in state["history"]
            ):
                raise ValueError("Rollback target has never been active")
            state["active"] = release_id
        else:
            raise ValueError("Unknown release transition")
        self._save(state, action, release_id)

    def active(self):
        state = self.read()
        if not state["active"]:
            raise ValueError("No active release")
        entry = state["releases"][state["active"]]
        if entry["status"] != "validated":
            raise ValueError("Release unavailable")
        return entry, self.bundle(entry), state["generation"]
