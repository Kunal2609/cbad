"""REST API for organizations, direct execution, exams and candidate submissions."""
import hashlib
import json
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Blueprint, current_app, g, jsonify, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import config
from app.integrations import store
from app.routes import get_executor
from app.security import ValidationError, validate_execute_request

bp = Blueprint("integration_v1", __name__, url_prefix="/api/v1")


def now():
    return datetime.now(timezone.utc)


def iso(value):
    return value.isoformat()


def parse_timestamp(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("timestamp must be text")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def error(code, message, status):
    return jsonify({"error": {"code": code, "message": message, "request_id": request.headers.get("X-Request-ID", str(uuid.uuid4()))}}), status


def auth(*required):
    def decorator(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            header = request.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                return error("AUTHENTICATION_REQUIRED", "A valid bearer credential is required.", 401)
            token = header[7:].strip()
            # Candidate tokens are short-lived signed tokens and are accepted only on candidate routes.
            if token.startswith("cand_"):
                try:
                    payload = _serializer().loads(token[5:], max_age=60 * 60 * 12)
                except (BadSignature, SignatureExpired):
                    return error("SESSION_EXPIRED", "The examination session has expired.", 401)
                with store.db() as c:
                    row = c.execute("SELECT * FROM sessions WHERE id=? AND organization_id=?", (payload.get("sid"), payload.get("org"))).fetchone()
                if not row or row["status"] != "active" or datetime.fromisoformat(row["expires_at"]) <= now():
                    return error("SESSION_EXPIRED", "The examination session has expired.", 401)
                g.session = dict(row)
                g.organization_id = row["organization_id"]
                g.scopes = {"candidate"}
            else:
                digest = hashlib.sha256(token.encode()).hexdigest()
                with store.db() as c:
                    row = c.execute("SELECT credentials.*,organizations.status AS org_status FROM credentials JOIN organizations ON organizations.id=credentials.organization_id WHERE key_hash=? AND revoked_at IS NULL", (digest,)).fetchone()
                if not row or row["org_status"] != "active":
                    return error("INVALID_CREDENTIAL", "The API credential is invalid or revoked.", 401)
                g.organization_id = row["organization_id"]
                g.scopes = set(json.loads(row["scopes"]))
            if any(scope not in g.scopes for scope in required):
                return error("INSUFFICIENT_SCOPE", "The credential does not grant the required scope.", 403)
            return fn(*args, **kwargs)
        return wrapped
    return decorator


def _serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="exam-candidate-session-v1")


def _json():
    if not request.is_json:
        return None
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else None


@bp.get("/health")
def health():
    return jsonify({"status": "healthy", "service": "secure-execution-platform", "api_version": "v1"})


@bp.get("/integrations/me")
@auth()
def integration_me():
    with store.db() as c:
        org = c.execute("SELECT id,name,status,created_at,allowed_languages,rate_limit FROM organizations WHERE id=?", (g.organization_id,)).fetchone()
    return jsonify({"organization": {**dict(org), "allowed_languages": json.loads(org["allowed_languages"])}, "scopes": sorted(g.scopes)})


@bp.delete("/integrations/credentials/<credential_id>")
@auth("credentials:write")
def revoke_credential(credential_id):
    with store.db() as c:
        cur = c.execute("UPDATE credentials SET revoked_at=? WHERE id=? AND organization_id=? AND revoked_at IS NULL", (iso(now()), credential_id, g.organization_id))
        if not cur.rowcount:
            return error("NOT_FOUND", "Credential not found.", 404)
        store.audit(c, g.organization_id, "credential_revoked", credential_id)
    return "", 204


@bp.post("/executions")
@auth("executions:write")
def execute():
    data = _json()
    if data is None or set(data) != {"code", "language"} or data.get("language") != "python":
        return error("INVALID_REQUEST", "Provide only code and supported language 'python'.", 400)
    try:
        code = validate_execute_request({"code": data["code"]})
    except (KeyError, ValidationError) as exc:
        return error("INVALID_REQUEST", getattr(exc, "message", "Invalid code."), 400)
    result = get_executor().execute(code)
    with store.db() as c:
        c.execute("INSERT INTO executions(id,organization_id,result,created_at) VALUES(?,?,?,?)", (result["execution_id"], g.organization_id, json.dumps(result), iso(now())))
        store.audit(c, g.organization_id, "execution_completed", result["execution_id"], {"status": result["status"]})
    return jsonify(result), 200


@bp.get("/executions/<execution_id>")
@auth("executions:read")
def execution_result(execution_id):
    with store.db() as c:
        row = c.execute("SELECT result FROM executions WHERE id=? AND organization_id=?", (execution_id, g.organization_id)).fetchone()
    if not row:
        return error("NOT_FOUND", "Execution not found.", 404)
    return jsonify(json.loads(row["result"]))


@bp.post("/exams")
@auth("exams:write")
def create_exam():
    data = _json() or {}
    allowed = {"title", "description", "start_time", "end_time", "allowed_languages", "time_limit", "source_limit", "submission_limit", "public_tests", "hidden_tests"}
    if set(data) - allowed or not isinstance(data.get("title"), str) or not data["title"].strip():
        return error("INVALID_REQUEST", "A title is required and unknown fields are not accepted.", 400)
    try:
        duration = int(data.get("time_limit", 3))
        source_limit = int(data.get("source_limit", config.MAX_CODE_SIZE))
        submission_limit = int(data.get("submission_limit", 10))
        langs = data.get("allowed_languages", ["python"])
        public, hidden = data.get("public_tests", []), data.get("hidden_tests", [])
        if duration < 1 or duration > config.EXECUTION_TIMEOUT or source_limit < 1 or source_limit > config.MAX_CODE_SIZE or not 1 <= submission_limit <= 100 or langs != ["python"]:
            raise ValueError()
        if not isinstance(public, list) or not isinstance(hidden, list) or any(not isinstance(t, dict) or set(t) != {"expected_stdout"} or not isinstance(t.get("expected_stdout"), str) for t in public + hidden):
            raise ValueError()
        start_time = parse_timestamp(data.get("start_time"))
        end_time = parse_timestamp(data.get("end_time"))
        if start_time and end_time and end_time <= start_time:
            raise ValueError()
    except (TypeError, ValueError):
        return error("INVALID_POLICY", "Exam policy exceeds platform limits or has invalid tests.", 400)
    exam_id = str(uuid.uuid4())
    with store.db() as c:
        c.execute("INSERT INTO exams VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (exam_id, g.organization_id, data["title"].strip(), str(data.get("description", ""))[:4000], "scheduled" if start_time else "draft", iso(start_time) if start_time else None, iso(end_time) if end_time else None, json.dumps(langs), duration, source_limit, submission_limit, json.dumps(public), json.dumps(hidden), iso(now())))
        store.audit(c, g.organization_id, "exam_created", exam_id)
    return jsonify({"exam_id": exam_id, "status": "scheduled" if start_time else "draft", "title": data["title"].strip()}), 201


def exam_view(row):
    return {"exam_id": row["id"], "title": row["title"], "description": row["description"], "status": row["status"], "start_time": row["start_time"], "end_time": row["end_time"], "allowed_languages": json.loads(row["allowed_languages"]), "time_limit": row["time_limit"], "source_limit": row["source_limit"], "submission_limit": row["submission_limit"], "public_tests": json.loads(row["public_tests"])}


@bp.get("/exams/<exam_id>")
@auth("exams:read")
def get_exam(exam_id):
    with store.db() as c:
        row = c.execute("SELECT * FROM exams WHERE id=? AND organization_id=?", (exam_id, g.organization_id)).fetchone()
    if not row:
        return error("NOT_FOUND", "Exam not found.", 404)
    return jsonify(exam_view(row))


@bp.post("/exams/<exam_id>/status")
@auth("exams:write")
def update_exam_status(exam_id):
    data = _json() or {}
    if set(data) != {"status"}:
        return error("INVALID_REQUEST", "Provide only the new exam status.", 400)
    transitions = {"draft": {"scheduled", "cancelled"}, "scheduled": {"active", "cancelled"}, "active": {"completed", "cancelled"}, "completed": set(), "cancelled": set()}
    with store.db() as c:
        exam = c.execute("SELECT status,start_time,end_time FROM exams WHERE id=? AND organization_id=?", (exam_id, g.organization_id)).fetchone()
        if not exam:
            return error("NOT_FOUND", "Exam not found.", 404)
        target = data["status"]
        if not isinstance(target, str):
            return error("INVALID_REQUEST", "Exam status must be text.", 400)
        if target not in transitions.get(exam["status"], set()):
            return error("INVALID_TRANSITION", "The requested exam status transition is not allowed.", 409)
        if target == "active" and ((exam["start_time"] and now() < parse_timestamp(exam["start_time"])) or (exam["end_time"] and now() >= parse_timestamp(exam["end_time"]))):
            return error("EXAM_UNAVAILABLE", "The examination is outside its availability window.", 409)
        c.execute("UPDATE exams SET status=? WHERE id=? AND organization_id=?", (target, exam_id, g.organization_id))
        store.audit(c, g.organization_id, "exam_activated" if target == "active" else "exam_status_changed", exam_id, {"status": target})
    return jsonify({"exam_id": exam_id, "status": target})


@bp.post("/exams/<exam_id>/sessions")
@auth("sessions:write")
def create_session(exam_id):
    data = _json() or {}
    candidate = data.get("external_candidate_reference")
    if not isinstance(candidate, str) or not candidate.strip() or len(candidate) > 200 or set(data) != {"external_candidate_reference"}:
        return error("INVALID_REQUEST", "A valid external_candidate_reference is required.", 400)
    with store.db() as c:
        exam = c.execute("SELECT * FROM exams WHERE id=? AND organization_id=?", (exam_id, g.organization_id)).fetchone()
        if not exam:
            return error("NOT_FOUND", "Exam not found.", 404)
        start = parse_timestamp(exam["start_time"])
        end = parse_timestamp(exam["end_time"])
        if exam["status"] != "active" or (start and now() < start) or (end and now() >= end):
            return error("EXAM_UNAVAILABLE", "The examination is not currently available.", 409)
        sid = str(uuid.uuid4())
        expires = min([x for x in (end, now() + timedelta(hours=4)) if x])
        try:
            c.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?,?)", (sid, g.organization_id, exam_id, candidate.strip(), iso(now()), iso(expires), "active"))
        except sqlite3.IntegrityError:
            return error("SESSION_EXISTS", "A session already exists for this candidate and exam.", 409)
        store.audit(c, g.organization_id, "session_created", sid)
    signed = _serializer().dumps({"sid": sid, "org": g.organization_id})
    return jsonify({"session_id": sid, "candidate_token": "cand_" + signed, "issued_at": iso(now()), "expires_at": iso(expires)}), 201


@bp.get("/exams/<exam_id>/sessions/<session_id>")
@auth("sessions:read")
def get_session(exam_id, session_id):
    with store.db() as c:
        row = c.execute("SELECT id,exam_id,candidate_ref,issued_at,expires_at,status FROM sessions WHERE id=? AND exam_id=? AND organization_id=?", (session_id, exam_id, g.organization_id)).fetchone()
    return (jsonify(dict(row)), 200) if row else error("NOT_FOUND", "Session not found.", 404)


@bp.post("/exams/<exam_id>/submissions")
@auth("candidate")
def submit(exam_id):
    session = g.session
    if session["exam_id"] != exam_id:
        return error("NOT_FOUND", "Session not found.", 404)
    data = _json()
    if data is None or set(data) != {"code", "language"} or data.get("language") != "python":
        return error("INVALID_REQUEST", "Provide only code and supported language 'python'.", 400)
    try:
        code = validate_execute_request({"code": data["code"]})
    except (KeyError, ValidationError) as exc:
        return error("INVALID_REQUEST", getattr(exc, "message", "Invalid code."), 400)
    idem = request.headers.get("Idempotency-Key")
    with store.db() as c:
        exam = c.execute("SELECT * FROM exams WHERE id=? AND organization_id=?", (exam_id, g.organization_id)).fetchone()
        if len(code.encode("utf-8")) > exam["source_limit"]:
            return error("SOURCE_TOO_LARGE", "Source exceeds this exam's limit.", 413)
        if exam["end_time"] and now() >= parse_timestamp(exam["end_time"]):
            return error("EXAM_ENDED", "The examination has ended.", 409)
        if idem:
            prior = c.execute("SELECT id,result,score,status FROM submissions WHERE organization_id=? AND session_id=? AND idempotency_key=?", (g.organization_id, session["id"], idem)).fetchone()
            if prior:
                return jsonify({"submission_id": prior["id"], "status": prior["status"], "result": json.loads(prior["result"]), "score": prior["score"]})
        count = c.execute("SELECT COUNT(*) FROM submissions WHERE session_id=?", (session["id"],)).fetchone()[0]
        if count >= exam["submission_limit"]:
            return error("SUBMISSION_LIMIT", "The submission limit has been reached.", 409)
        hidden = json.loads(exam["hidden_tests"])
    # Hidden tests use server-side output expectations; test data is never added to
    # the student's source file or returned in a candidate response.
    execution = get_executor().execute(code, execution_timeout=exam["time_limit"])
    passed = execution.get("status") == "completed" and execution.get("exit_code") == 0 and bool(hidden) and any(
        execution.get("stdout", "") == test["expected_stdout"] for test in hidden
    )
    result = {"execution_id": execution["execution_id"], "status": execution["status"], "stdout": execution.get("stdout", ""), "stderr": execution.get("stderr", ""), "exit_code": execution.get("exit_code"), "execution_time_ms": execution.get("execution_time_ms"), "truncated": execution.get("truncated", False), "tests_passed": 1 if passed else 0, "tests_total": 1 if hidden else 0}
    score = 100.0 if passed else 0.0
    submission_id = str(uuid.uuid4())
    with store.db() as c:
        c.execute("INSERT INTO submissions VALUES(?,?,?,?,?,?,?,?,?,?)", (submission_id, g.organization_id, exam_id, session["id"], idem, code, "completed", json.dumps(result), score, iso(now())))
        store.audit(c, g.organization_id, "submission_received", submission_id)
        store.audit(c, g.organization_id, "assessment_completed", submission_id, {"score": score})
    return jsonify({"submission_id": submission_id, "status": "completed", "result": result, "score": score}), 201


@bp.get("/submissions/<submission_id>")
@auth()
def get_submission(submission_id):
    if "candidate" not in g.scopes and "submissions:read" not in g.scopes:
        return error("INSUFFICIENT_SCOPE", "The credential does not grant the required scope.", 403)
    with store.db() as c:
        row = c.execute("SELECT id,result,score,status,created_at,session_id FROM submissions WHERE id=? AND organization_id=?", (submission_id, g.organization_id)).fetchone()
    if not row or ("candidate" in g.scopes and row["session_id"] != g.session["id"]):
        return error("NOT_FOUND", "Submission not found.", 404)
    return jsonify({"submission_id": row["id"], "status": row["status"], "result": json.loads(row["result"]), "score": row["score"], "created_at": row["created_at"]})


@bp.get("/audit-events")
@auth("audit:read")
def audit_events():
    with store.db() as c:
        rows = c.execute("SELECT id,event_type,resource_id,metadata,created_at FROM audit_events WHERE organization_id=? ORDER BY id DESC LIMIT 100", (g.organization_id,)).fetchall()
    return jsonify({"events": [{**dict(row), "metadata": json.loads(row["metadata"])} for row in rows]})


@bp.post("/proctoring/events")
@auth("proctoring:write")
def proctoring_event():
    data = _json() or {}
    allowed_types = {"session_started", "session_ended", "focus_lost", "focus_restored", "provider_flagged_event"}
    if set(data) != {"session_id", "provider_event_id", "event_type", "occurred_at"} or data.get("event_type") not in allowed_types:
        return error("INVALID_EVENT", "The provider event schema is invalid.", 400)
    if not all(isinstance(data.get(k), str) and 0 < len(data[k]) <= 200 for k in ("session_id", "provider_event_id")):
        return error("INVALID_EVENT", "The provider event identifiers are invalid.", 400)
    try:
        occurred = parse_timestamp(data.get("occurred_at"))
    except (TypeError, ValueError):
        return error("INVALID_EVENT", "occurred_at must be an ISO-8601 timestamp with timezone.", 400)
    if abs((now() - occurred).total_seconds()) > 300:
        return error("STALE_EVENT", "The provider event is outside the accepted time window.", 409)
    event_id = str(uuid.uuid4())
    with store.db() as c:
        session = c.execute("SELECT id FROM sessions WHERE id=? AND organization_id=?", (data["session_id"], g.organization_id)).fetchone()
        if not session:
            return error("NOT_FOUND", "Session not found.", 404)
        try:
            c.execute("INSERT INTO proctor_events VALUES(?,?,?,?,?,?,?)", (event_id, g.organization_id, data["session_id"], data["provider_event_id"], data["event_type"], iso(occurred), iso(now())))
        except sqlite3.IntegrityError:
            return error("REPLAYED_EVENT", "This provider event was already received.", 409)
        store.audit(c, g.organization_id, "security_event_received", data["session_id"], {"event_type": data["event_type"], "provider_event_id": data["provider_event_id"]})
    return jsonify({"event_id": event_id, "accepted": True, "note": "Provider events are indicators and are not conclusive evidence of misconduct."}), 202


def provision_organization(app):
    """Register CLI bootstrap commands; raw credentials are emitted only once."""
    @app.cli.command("provision-organization")
    def provision_command():
        import click
        name = click.prompt("Organization name")
        org_id, credential_id = str(uuid.uuid4()), str(uuid.uuid4())
        secret = "scp_" + secrets.token_urlsafe(32)
        scopes = ["executions:write", "executions:read", "exams:write", "exams:read", "sessions:write", "sessions:read", "submissions:read", "audit:read", "proctoring:write", "credentials:write"]
        with store.db() as c:
            c.execute("INSERT INTO organizations(id,name,created_at) VALUES(?,?,?)", (org_id, name, iso(now())))
            c.execute("INSERT INTO credentials VALUES(?,?,?,?,?,NULL)", (credential_id, org_id, hashlib.sha256(secret.encode()).hexdigest(), json.dumps(scopes), iso(now())))
            store.audit(c, org_id, "organization_created", org_id)
            store.audit(c, org_id, "credential_created", credential_id)
        click.echo(f"Organization ID: {org_id}\nCredential ID: {credential_id}\nAPI key (shown once): {secret}")

    @app.cli.command("issue-integration-credential")
    def issue_credential_command():
        import click
        organization_id = click.prompt("Organization ID")
        credential_id = str(uuid.uuid4())
        secret = "scp_" + secrets.token_urlsafe(32)
        scopes = ["executions:write", "executions:read", "exams:write", "exams:read", "sessions:write", "sessions:read", "submissions:read", "audit:read", "proctoring:write", "credentials:write"]
        with store.db() as c:
            if not c.execute("SELECT 1 FROM organizations WHERE id=? AND status='active'", (organization_id,)).fetchone():
                raise click.ClickException("Active organization not found.")
            c.execute("INSERT INTO credentials VALUES(?,?,?,?,?,NULL)", (credential_id, organization_id, hashlib.sha256(secret.encode()).hexdigest(), json.dumps(scopes), iso(now())))
            store.audit(c, organization_id, "credential_created", credential_id)
        click.echo(f"Credential ID: {credential_id}\nAPI key (shown once): {secret}")

