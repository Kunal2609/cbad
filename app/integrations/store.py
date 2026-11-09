"""Small transactional persistence layer for integration and assessment records.

SQLite is the zero-configuration development default. Set INTEGRATION_DATABASE to
a SQLite file path in production; the service deliberately keeps tenant checks in
every query rather than trusting caller supplied organization identifiers.
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def _database_path():
    return os.getenv("INTEGRATION_DATABASE", str(Path(__file__).resolve().parents[2] / "instance" / "integration.sqlite3"))


@contextmanager
def db():
    path = _database_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS organizations(id TEXT PRIMARY KEY,name TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active',created_at TEXT NOT NULL,allowed_languages TEXT NOT NULL DEFAULT '[\"python\"]',rate_limit INTEGER NOT NULL DEFAULT 60);
        CREATE TABLE IF NOT EXISTS credentials(id TEXT PRIMARY KEY,organization_id TEXT NOT NULL REFERENCES organizations(id),key_hash TEXT NOT NULL UNIQUE,scopes TEXT NOT NULL,created_at TEXT NOT NULL,revoked_at TEXT);
        CREATE TABLE IF NOT EXISTS exams(id TEXT PRIMARY KEY,organization_id TEXT NOT NULL REFERENCES organizations(id),title TEXT NOT NULL,description TEXT NOT NULL,status TEXT NOT NULL,start_time TEXT,end_time TEXT,allowed_languages TEXT NOT NULL,time_limit INTEGER NOT NULL,source_limit INTEGER NOT NULL,submission_limit INTEGER NOT NULL,public_tests TEXT NOT NULL,hidden_tests TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,organization_id TEXT NOT NULL,exam_id TEXT NOT NULL REFERENCES exams(id),candidate_ref TEXT NOT NULL,issued_at TEXT NOT NULL,expires_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'active',UNIQUE(exam_id,candidate_ref));
        CREATE TABLE IF NOT EXISTS submissions(id TEXT PRIMARY KEY,organization_id TEXT NOT NULL,exam_id TEXT NOT NULL,session_id TEXT NOT NULL REFERENCES sessions(id),idempotency_key TEXT,code TEXT NOT NULL,status TEXT NOT NULL,result TEXT NOT NULL,score REAL NOT NULL,created_at TEXT NOT NULL,UNIQUE(organization_id,session_id,idempotency_key));
        CREATE TABLE IF NOT EXISTS executions(id TEXT PRIMARY KEY,organization_id TEXT NOT NULL,result TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit_events(id INTEGER PRIMARY KEY AUTOINCREMENT,organization_id TEXT NOT NULL,event_type TEXT NOT NULL,resource_id TEXT,metadata TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS proctor_events(id TEXT PRIMARY KEY,organization_id TEXT NOT NULL,session_id TEXT NOT NULL REFERENCES sessions(id),provider_event_id TEXT NOT NULL,event_type TEXT NOT NULL,occurred_at TEXT NOT NULL,received_at TEXT NOT NULL,UNIQUE(organization_id,provider_event_id));
        CREATE INDEX IF NOT EXISTS idx_exams_org ON exams(organization_id);
        CREATE INDEX IF NOT EXISTS idx_sessions_exam ON sessions(organization_id,exam_id);
        CREATE INDEX IF NOT EXISTS idx_submissions_org ON submissions(organization_id,session_id);
        CREATE INDEX IF NOT EXISTS idx_audit_org ON audit_events(organization_id,created_at);
        """)


def audit(c, org, event, resource=None, metadata=None):
    from datetime import datetime, timezone
    c.execute("INSERT INTO audit_events(organization_id,event_type,resource_id,metadata,created_at) VALUES(?,?,?,?,?)",
              (org, event, resource, json.dumps(metadata or {}, separators=(",", ":")), datetime.now(timezone.utc).isoformat()))
