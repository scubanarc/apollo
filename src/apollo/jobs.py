"""Durable local job queue. One worker executes operations in submission order."""

import fcntl
import json
import logging
import signal
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from apollo.services import validate


def now():
    return datetime.now(UTC).isoformat()


class JobStore:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory = directory
        self.path = directory / "jobs.sqlite3"
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, action TEXT NOT NULL, payload TEXT NOT NULL,
                status TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL,
                result TEXT, error TEXT, logs TEXT NOT NULL DEFAULT '[]')""")
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def submit(self, action, payload):
        payload = validate(action, payload)
        identifier = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            pending = db.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')"
            ).fetchone()[0]
            if pending >= 100:
                raise ValueError("The job queue is full. Wait for current work to finish.")
            db.execute(
                "INSERT INTO jobs (id,action,payload,status,created,updated) VALUES (?,?,?,?,?,?)",
                (identifier, action, json.dumps(payload), "queued", now(), now()),
            )
        return self.get(identifier)

    def get(self, identifier):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise KeyError(identifier)
        result = dict(row)
        for key in ("payload", "result", "logs"):
            result[key] = json.loads(result[key]) if result[key] else None
        return result

    def list(self, limit=50, offset=0):
        with self.connect() as db:
            rows = db.execute(
                "SELECT id,action,status,created,updated,error FROM jobs ORDER BY created DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            total = db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        return dict(items=[dict(r) for r in rows], total=total, limit=limit, offset=offset)

    def claim(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id FROM jobs WHERE status='queued' ORDER BY created LIMIT 1"
            ).fetchone()
            if not row:
                return None
            db.execute("UPDATE jobs SET status='running', updated=? WHERE id=?", (now(), row["id"]))
        return self.get(row["id"])

    def finish(self, identifier, result=None, error=None):
        partial = isinstance(result, dict) and bool(result.get("failed") or result.get("errors"))
        status = "failed" if error else "partial" if partial else "succeeded"
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET status=?,result=?,error=?,updated=? WHERE id=?",
                (status, json.dumps(result, default=str), error, now(), identifier),
            )

    def append_log(self, identifier, message):
        with self.connect() as db:
            row = db.execute("SELECT logs FROM jobs WHERE id=?", (identifier,)).fetchone()
            logs = (json.loads(row["logs"]) + [message[:2000]])[-200:]
            db.execute(
                "UPDATE jobs SET logs=?, updated=? WHERE id=?",
                (json.dumps(logs), now(), identifier),
            )

    def recover(self):
        with self.connect() as db:
            db.execute(
                "UPDATE jobs SET status='failed', error='Worker stopped during execution. Inspect results before retrying; partial changes may exist.', updated=? WHERE status='running'",
                (now(),),
            )

    def worker_alive(self):
        with (self.directory / "worker.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return False
            except BlockingIOError:
                return True


class JobLog(logging.Handler):
    def __init__(self, store, identifier, service):
        super().__init__(logging.INFO)
        self.store, self.identifier, self.service = store, identifier, service

    def emit(self, record):
        self.store.append_log(self.identifier, self.service.redact(record.getMessage()))


def run_worker(service, store, once=False):
    stopped = threading.Event()
    with (store.directory / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        store.recover()
        previous = {}
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGTERM, signal.SIGINT):
                previous[sig] = signal.signal(sig, lambda *_: stopped.set())
        try:
            while not stopped.is_set():
                job = store.claim()
                if job is None:
                    if once:
                        return
                    stopped.wait(0.5)
                    continue
                handler = JobLog(store, job["id"], service)
                logger = logging.getLogger("apollo")
                logger.addHandler(handler)
                logger.setLevel(logging.INFO)
                try:
                    store.finish(job["id"], result=service.execute(job["action"], job["payload"]))
                except Exception as exc:
                    store.finish(job["id"], error=service.redact(exc))
                finally:
                    logger.removeHandler(handler)
                if once:
                    return
        finally:
            for sig, previous_handler in previous.items():
                signal.signal(sig, previous_handler)
