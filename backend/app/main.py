"""
Routes only. No business logic lives here -- validate the request, call a
module, record an audit entry, return. All SQL lives in storage.py.
"""
import base64
import secrets
from datetime import datetime, timezone

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel

from . import (
    audit, checks, config, dashboard, identity, intel_extract, llm,
    report as report_module, review, storage, taxonomy,
)

app = FastAPI(title="Nova-CCU")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if config.ENVIRONMENT != "production" else [],
    allow_methods=["*"],
    allow_headers=["*"],
)

if config.msal_configured():
    app.add_middleware(SessionMiddleware, secret_key=config.SESSION_SECRET_KEY)
elif config.AZURE_AD_CLIENT_ID or config.AZURE_AD_TENANT_ID:
    # Sign-in partially configured but not enough to actually verify a
    # session -- fail closed rather than silently falling back to dev auth.
    raise RuntimeError(
        "Entra sign-in is partially configured (client ID or tenant ID set) "
        "but msal_configured() is False. Set all of AZURE_AD_CLIENT_ID, "
        "AZURE_AD_CLIENT_SECRET, AZURE_AD_TENANT_ID and SESSION_SECRET_KEY, "
        "or unset all of them to run in dev-fallback mode."
    )


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """
    taxonomy.validate() raises plain ValueError on an off-list value.
    Without this handler that becomes an unhandled 500 instead of a clean
    client error -- found while seeding test data with a malformed
    category value.
    """
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.on_event("startup")
def on_startup():
    storage.init_db()
    _seed_known_officers()


_KNOWN_OFFICERS = [
    # principal, display name, role, demo password -- see README "Demo sign-ins"
    ("ds.hooker@example.police.uk", "DS R. Hooker 341", "supervisor", "Hooker#4471"),
    ("dc.marsh@example.police.uk", "DC J. Marsh", "officer", "Marsh#2290"),
    ("di.grainger@example.police.uk", "DI Grainger", "officer", "Grainger#8813"),
    ("intelligence.cell@example.police.uk", "Intelligence Cell", "officer", "IntelCell#5541"),
]


def _seed_known_officers():
    for principal, name, role, password in _KNOWN_OFFICERS:
        user = storage.get_user_by_principal(principal)
        if user is None:
            user = storage.create_user(principal, name, role, "dev-fallback")
        if not user["password_hash"]:
            storage.set_user_password(principal, identity.hash_password(password))


@app.middleware("http")
async def require_site_password(request: Request, call_next):
    """
    Optional shared front-door gate for a semi-public deployment (e.g. a
    Render URL shared with a few colleagues) that has no real per-user
    auth yet -- see identity.py's dev-fallback docstring. Off by default
    (blank NOVA_CCU_SITE_PASSWORD); dev-fallback identity still applies
    underneath it -- this is a coarse "right visitor" check, not a
    substitute for real sign-in. /api/health stays open so the hosting
    platform's own health check (which sends no credentials) still passes.
    """
    if not config.site_gate_configured() or request.url.path == "/api/health":
        return await call_next(request)

    from fastapi.responses import Response

    auth = request.headers.get("Authorization", "")
    if auth.startswith("Basic "):
        try:
            username, _, password = base64.b64decode(auth[6:]).decode("utf-8").partition(":")
        except Exception:
            username, password = "", ""
        if secrets.compare_digest(username, config.SITE_USERNAME) and secrets.compare_digest(password, config.SITE_PASSWORD):
            return await call_next(request)

    return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Nova-CCU"'})


@app.middleware("http")
async def set_audit_context(request: Request, call_next):
    """Every request resolves an identity (or none) and sets the actor
    context so audit.record() never needs the caller to pass it."""
    ident = identity.resolve_identity(request)
    if ident:
        audit.current_actor.set(ident.principal)
        audit.current_identity_source.set(ident.identity_source)
    else:
        audit.current_actor.set("anonymous")
        audit.current_identity_source.set("unknown")
    return await call_next(request)


# ---------------------------------------------------------------------------
# Health / mode
# ---------------------------------------------------------------------------

@app.get("/api/attestation-statements")
def get_attestation_statements():
    return review.ATTESTATION_STATEMENTS


@app.get("/api/health")
def health():
    ok, broken_id = audit.verify_chain()
    return {
        "status": "ok",
        "modes": config.all_modes(),
        "audit_chain_ok": ok,
        "audit_chain_broken_at": broken_id,
    }


@app.get("/api/taxonomy")
def get_taxonomy():
    """So the frontend never hardcodes a controlled vocabulary independently."""
    return {
        "stages": taxonomy.STAGES,
        "hub_stages": taxonomy.HUB_STAGES,
        "risk_levels": taxonomy.RISK_LEVELS,
        "source_types": taxonomy.SOURCE_TYPES,
        "intel_categories": taxonomy.INTEL_CATEGORIES,
        "source_evaluation": taxonomy.SOURCE_EVALUATION,
        "intelligence_evaluation": taxonomy.INTELLIGENCE_EVALUATION,
        "handling_codes": taxonomy.HANDLING_CODES,
        "handling_conditions": taxonomy.HANDLING_CONDITIONS,
        "gsc_levels": taxonomy.GSC_LEVELS,
        "adc_decisions": taxonomy.ADC_DECISIONS,
        "action_priorities": taxonomy.ACTION_PRIORITIES,
        "action_statuses": taxonomy.ACTION_STATUSES,
        "update_entry_roles": taxonomy.UPDATE_ENTRY_ROLES,
        "impact_labels": taxonomy.IMPACT_LABELS,
        "likelihood_labels": taxonomy.LIKELIHOOD_LABELS,
    }


@app.get("/api/whoami")
def whoami(request: Request):
    ident = identity.resolve_identity(request)
    if ident is None:
        return {"signed_in": False}
    return {
        "signed_in": True,
        "principal": ident.principal,
        "display_name": ident.display_name,
        "role": ident.role,
        "identity_source": ident.identity_source,
    }


class DevLoginBody(BaseModel):
    principal: str
    password: str = ""


@app.post("/api/auth/dev-login")
def dev_login(body: DevLoginBody):
    """
    Password check in front of dev-fallback sign-in, for the named demo
    accounts only (see README "Demo sign-ins"). A principal with no
    password_hash set (any ad-hoc email typed into "Switch user") signs in
    exactly as before -- this never closes off the "type any email to test
    as that officer" convenience the rest of dev-fallback relies on.

    This is a UX gate, not a new access-control boundary: no session or
    token is issued, and every other route still trusts whatever
    X-User-Principal header a request carries, same as before this existed.
    """
    if config.ENVIRONMENT == "production":
        raise HTTPException(403, "Dev sign-in is not available in production.")
    user = storage.get_user_by_principal(body.principal.strip())
    if user is None or not user["password_hash"]:
        return {"ok": True}
    if not identity.verify_password(body.password, user["password_hash"]):
        raise HTTPException(422, "Incorrect password for this account.")
    return {"ok": True}


# ---------------------------------------------------------------------------
# AI-drafted intelligence from an uploaded document -- draft only, never
# saved here. The officer reviews every field in the ordinary Record
# Intelligence form and presses Create themselves; see intel_extract.py.
# ---------------------------------------------------------------------------

MAX_UPLOAD_BYTES = 8 * 1024 * 1024  # 8MB -- a single email/report, not a case file store


@app.post("/api/intel/extract")
async def extract_intel_draft(request: Request, file: UploadFile = File(...)):
    identity.require_identity(request)
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File is too large (8MB limit).")

    text = intel_extract.extract_text(file.filename or "", content)  # ValueError -> 422 via the handler below
    if not text.strip():
        raise HTTPException(422, "No readable text was found in this file.")

    draft = await intel_extract.draft_intel_from_text(text)
    audit.record("intel.ai_draft_requested", detail={"filename": file.filename, "llm_mode": draft["llm_mode"]})
    return draft


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

class CreateCaseBody(BaseModel):
    subject_code: str
    source: str
    category: str
    source_evaluation: str
    intelligence_evaluation: str
    handling_code: str
    handling_conditions: str = "None"
    gsc: str = "OFFICIAL-SENSITIVE"
    source_reference: str = ""
    crime_ref: str = ""
    sanitised: str = ""
    review_date: str
    summary: str


def _next_ref(prefix: str, table: str, ref_col: str = "ref") -> str:
    conn = storage.get_conn()
    rows = conn.execute(f"SELECT {ref_col} FROM {table}").fetchall()
    nums = []
    for r in rows:
        try:
            nums.append(int(r[ref_col].split("/")[1]))
        except (IndexError, ValueError):
            pass
    return f"{prefix}/{(max(nums) + 1) if nums else 1}/26"


@app.post("/api/cases")
def create_case(body: CreateCaseBody, request: Request):
    ident = identity.require_identity(request)
    taxonomy.validate(body.source, taxonomy.SOURCE_TYPES, "source type")
    taxonomy.validate(body.category, taxonomy.INTEL_CATEGORIES, "category")
    taxonomy.validate(body.source_evaluation, taxonomy.SOURCE_EVALUATION, "source evaluation")
    taxonomy.validate(body.intelligence_evaluation, taxonomy.INTELLIGENCE_EVALUATION, "intelligence evaluation")
    taxonomy.validate(body.handling_code, taxonomy.HANDLING_CODES, "handling code")
    taxonomy.validate(body.gsc, taxonomy.GSC_LEVELS, "GSC level")
    if not body.summary.strip():
        raise HTTPException(422, "Summary cannot be empty.")

    subject_id = storage.get_or_create_subject(body.subject_code)
    ref = _next_ref("CCU", "cases")
    urn = _next_ref("INT", "intelligence", ref_col="urn")
    user = storage.get_user_by_principal(ident.principal)
    now = datetime.now(timezone.utc).isoformat()

    with storage.tx() as conn:
        cur = conn.execute(
            """INSERT INTO cases (ref, subject_id, stage_index, officer_id, source, category, created_by)
               VALUES (?, ?, 0, NULL, ?, ?, ?)""",
            (ref, subject_id, body.source, body.category, user["id"]),
        )
        case_id = cur.lastrowid

        conn.execute(
            """INSERT INTO intelligence
               (case_id, urn, received_at, source_type, gsc, source_reference, source_evaluation,
                intelligence_evaluation, handling_code, handling_conditions, risk_to_source,
                crime_ref, sanitised, review_date, summary, submitting_officer_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'No', ?, ?, ?, ?, ?)""",
            (case_id, urn, now, body.source, body.gsc, body.source_reference, body.source_evaluation,
             body.intelligence_evaluation, body.handling_code, body.handling_conditions,
             body.crime_ref, body.sanitised, body.review_date, body.summary.strip(), user["id"]),
        )

    audit.record("case.created", item_id=str(case_id), case_ref=ref)
    return {"id": case_id, "ref": ref, "urn": urn, "officer": "OIC TBC"}


@app.get("/api/users")
def list_users(request: Request):
    identity.require_identity(request)
    conn = storage.get_conn()
    rows = conn.execute("SELECT principal, display_name, role FROM users ORDER BY display_name").fetchall()
    return [dict(r) for r in rows]


@app.get("/api/cases")
def list_cases(request: Request):
    identity.require_identity(request)  # must be signed in; visibility is deliberately not restricted -- all officers see all files
    conn = storage.get_conn()
    rows = conn.execute(
        """SELECT cases.*, subjects.code AS subject_code, u.display_name AS officer_name, u.principal AS officer_principal
            FROM cases JOIN subjects ON subjects.id = cases.subject_id
            LEFT JOIN users u ON u.id = cases.officer_id
            ORDER BY cases.id DESC"""
    ).fetchall()
    result = [dict(r) for r in rows]
    for r, row in zip(result, rows):
        r["stage_index"] = dashboard.compute_stage_index(conn, row["id"], row)
    return result


class ReassignOfficerBody(BaseModel):
    officer_principal: str | None  # None = OIC TBC


@app.get("/api/cases/{case_id}")
def get_case(case_id: int, request: Request):
    identity.require_identity(request)  # visibility deliberately not restricted -- all officers see all files
    conn = storage.get_conn()
    case = conn.execute(
        """SELECT cases.*, subjects.code AS subject_code, u.display_name AS officer_name, u.principal AS officer_principal
           FROM cases JOIN subjects ON subjects.id = cases.subject_id
           LEFT JOIN users u ON u.id = cases.officer_id
           WHERE cases.id = ?""",
        (case_id,),
    ).fetchone()
    if case is None:
        raise HTTPException(404, "Case not found.")
    case = dict(case)
    case["stage_index"] = dashboard.compute_stage_index(conn, case_id, case)

    intel = conn.execute("SELECT * FROM intelligence WHERE case_id = ?", (case_id,)).fetchone()
    adc = conn.execute(
        """SELECT adc_decisions.*, u.display_name AS action_owner_name
           FROM adc_decisions LEFT JOIN users u ON u.id = adc_decisions.action_owner_id
           WHERE case_id = ?""",
        (case_id,),
    ).fetchone()
    investigation = conn.execute("SELECT * FROM investigations WHERE case_id = ?", (case_id,)).fetchone()
    actions = conn.execute(
        """SELECT actions.*, u.display_name AS owner_name FROM actions
           LEFT JOIN users u ON u.id = actions.owner_id
           WHERE case_id = ? ORDER BY actions.id""",
        (case_id,),
    ).fetchall()
    updates = conn.execute(
        """SELECT case_updates.*, u.display_name AS entered_by_name FROM case_updates
           JOIN users u ON u.id = case_updates.entered_by
           WHERE case_id = ? ORDER BY created_at""",
        (case_id,),
    ).fetchall()
    reports = conn.execute(
        "SELECT id, status, created_at FROM reports WHERE case_id = ? ORDER BY id DESC", (case_id,)
    ).fetchall()

    return {
        "case": dict(case),
        "intel": dict(intel) if intel else None,
        "adc": dict(adc) if adc else None,
        "investigation": dict(investigation) if investigation else None,
        "actions": [dict(a) for a in actions],
        "updates": [dict(u) for u in updates],
        "reports": [dict(r) for r in reports],
    }


@app.post("/api/cases/{case_id}/officer")
def reassign_officer(case_id: int, body: ReassignOfficerBody, request: Request):
    ident = identity.require_role(request, "supervisor")  # only a supervisor allocates
    officer_id = None
    if body.officer_principal:
        user = storage.get_user_by_principal(body.officer_principal)
        if not user:
            raise HTTPException(404, "Officer not found.")
        officer_id = user["id"]

    conn = storage.get_conn()
    case = conn.execute("SELECT ref FROM cases WHERE id = ?", (case_id,)).fetchone()
    if case is None:
        raise HTTPException(404, "Case not found.")

    conn.execute("UPDATE cases SET officer_id = ?, updated_at = ? WHERE id = ?",
                 (officer_id, datetime.now(timezone.utc).isoformat(), case_id))
    conn.commit()
    audit.record("case.officer_reassigned", item_id=str(case_id), case_ref=case["ref"])
    return {"ok": True}


class RiskUpdateBody(BaseModel):
    impact: int
    likelihood: int
    rationale: str


@app.post("/api/cases/{case_id}/risk")
def update_risk(case_id: int, body: RiskUpdateBody, request: Request):
    identity.require_identity(request)
    if not (1 <= body.impact <= 5 and 1 <= body.likelihood <= 5):
        raise HTTPException(422, "Impact and likelihood must each be 1-5.")

    conn = storage.get_conn()
    case = conn.execute("SELECT ref FROM cases WHERE id = ?", (case_id,)).fetchone()
    if case is None:
        raise HTTPException(404, "Case not found.")

    conn.execute(
        "UPDATE cases SET risk_impact = ?, risk_likelihood = ?, risk_rationale = ? WHERE id = ?",
        (body.impact, body.likelihood, body.rationale, case_id),
    )
    conn.commit()
    audit.record("case.risk_updated", item_id=str(case_id), case_ref=case["ref"])
    return {"band": taxonomy.risk_band_from_score(body.impact, body.likelihood)}


class GenerateRationaleBody(BaseModel):
    impact: int
    likelihood: int


@app.post("/api/cases/{case_id}/risk/generate-rationale")
async def generate_risk_rationale_route(case_id: int, body: GenerateRationaleBody, request: Request):
    identity.require_identity(request)
    if not (1 <= body.impact <= 5 and 1 <= body.likelihood <= 5):
        raise HTTPException(422, "Impact and likelihood must each be 1-5.")

    conn = storage.get_conn()
    case = conn.execute("SELECT ref FROM cases WHERE id = ?", (case_id,)).fetchone()
    if case is None:
        raise HTTPException(404, "Case not found.")

    rationale = await report_module.generate_risk_rationale(case_id, body.impact, body.likelihood)
    audit.record("case.risk_rationale_drafted", item_id=str(case_id), case_ref=case["ref"])
    return {"rationale": rationale, "llm_mode": llm.mode()}


class AdcDecisionBody(BaseModel):
    decision: str
    rationale: str
    action_owner_principal: str


@app.post("/api/cases/{case_id}/adc")
def record_adc_decision(case_id: int, body: AdcDecisionBody, request: Request):
    ident = identity.require_role(request, "supervisor")  # ADC decisions are a supervisor act
    taxonomy.validate(body.decision, taxonomy.ADC_DECISIONS, "ADC decision")

    conn = storage.get_conn()
    case = conn.execute("SELECT ref FROM cases WHERE id = ?", (case_id,)).fetchone()
    if case is None:
        raise HTTPException(404, "Case not found.")

    owner = storage.get_user_by_principal(body.action_owner_principal)
    decider = storage.get_user_by_principal(ident.principal)

    conn.execute(
        """INSERT INTO adc_decisions (case_id, considered_at, decision, rationale, action_owner_id, decided_by)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(case_id) DO UPDATE SET
             decision=excluded.decision, rationale=excluded.rationale,
             action_owner_id=excluded.action_owner_id, decided_by=excluded.decided_by,
             decided_at=datetime('now')""",
        (case_id, datetime.now(timezone.utc).isoformat(), body.decision, body.rationale,
         owner["id"] if owner else None, decider["id"]),
    )
    conn.commit()
    audit.record("case.adc_decision", item_id=str(case_id), case_ref=case["ref"], detail={"decision": body.decision})
    return {"ok": True}


class GenerateAdcRationaleBody(BaseModel):
    decision: str


@app.post("/api/cases/{case_id}/adc/generate-rationale")
async def generate_adc_rationale_route(case_id: int, body: GenerateAdcRationaleBody, request: Request):
    identity.require_role(request, "supervisor")  # matches who may actually record the decision
    taxonomy.validate(body.decision, taxonomy.ADC_DECISIONS, "ADC decision")

    conn = storage.get_conn()
    case = conn.execute("SELECT ref FROM cases WHERE id = ?", (case_id,)).fetchone()
    if case is None:
        raise HTTPException(404, "Case not found.")

    rationale = await report_module.generate_adc_rationale(case_id, body.decision)
    audit.record("case.adc_rationale_drafted", item_id=str(case_id), case_ref=case["ref"])
    return {"rationale": rationale, "llm_mode": llm.mode()}


class AddActionBody(BaseModel):
    title: str
    owner_principal: str
    priority: str
    due_date: str


@app.post("/api/cases/{case_id}/actions")
def add_action(case_id: int, body: AddActionBody, request: Request):
    identity.require_identity(request)  # deliberately open to any signed-in user
    taxonomy.validate(body.priority, taxonomy.ACTION_PRIORITIES, "priority")

    conn = storage.get_conn()
    case = conn.execute("SELECT ref FROM cases WHERE id = ?", (case_id,)).fetchone()
    if case is None:
        raise HTTPException(404, "Case not found.")
    owner = storage.get_user_by_principal(body.owner_principal)

    cur = conn.execute(
        "INSERT INTO actions (case_id, title, owner_id, priority, due_date) VALUES (?, ?, ?, ?, ?)",
        (case_id, body.title, owner["id"] if owner else None, body.priority, body.due_date),
    )
    conn.commit()
    audit.record("case.action_added", item_id=str(cur.lastrowid), case_ref=case["ref"])
    return {"id": cur.lastrowid}


class ActionStatusBody(BaseModel):
    status: str


@app.post("/api/actions/{action_id}/status")
def set_action_status(action_id: int, body: ActionStatusBody, request: Request):
    identity.require_identity(request)
    taxonomy.validate(body.status, taxonomy.ACTION_STATUSES, "action status")

    conn = storage.get_conn()
    row = conn.execute(
        "SELECT actions.case_id, cases.ref FROM actions JOIN cases ON cases.id = actions.case_id WHERE actions.id = ?",
        (action_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Action not found.")

    conn.execute("UPDATE actions SET status = ? WHERE id = ?", (body.status, action_id))
    conn.commit()
    audit.record("case.action_status_changed", item_id=str(action_id), case_ref=row["ref"])
    return {"ok": True}


class AddUpdateBody(BaseModel):
    text: str
    role_label: str  # OIC / Supervisor / Inspector -- see taxonomy.UPDATE_ENTRY_ROLES


@app.post("/api/cases/{case_id}/updates")
def add_update(case_id: int, body: AddUpdateBody, request: Request):
    """
    Insert-only. There is deliberately no PATCH/DELETE route for
    case_updates anywhere in this file -- that omission is the control.
    """
    ident = identity.require_identity(request)
    taxonomy.validate(body.role_label, taxonomy.UPDATE_ENTRY_ROLES, "update entry role")
    if not body.text.strip():
        raise HTTPException(422, "Update text cannot be empty.")

    conn = storage.get_conn()
    case = conn.execute("SELECT ref FROM cases WHERE id = ?", (case_id,)).fetchone()
    if case is None:
        raise HTTPException(404, "Case not found.")
    user = storage.get_user_by_principal(ident.principal)

    cur = conn.execute(
        "INSERT INTO case_updates (case_id, entered_by, entered_by_role_label, text) VALUES (?, ?, ?, ?)",
        (case_id, user["id"], body.role_label, body.text.strip()),
    )
    conn.commit()
    audit.record("case.update_added", item_id=str(cur.lastrowid), case_ref=case["ref"])
    return {"id": cur.lastrowid}


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

@app.post("/api/cases/{case_id}/report/generate")
async def generate_report_route(case_id: int, request: Request):
    ident = identity.require_identity(request)
    conn = storage.get_conn()
    case = conn.execute("SELECT ref FROM cases WHERE id = ?", (case_id,)).fetchone()
    if case is None:
        raise HTTPException(404, "Case not found.")

    result = await report_module.generate_report(case_id)
    user = storage.get_user_by_principal(ident.principal)

    import json

    cur = conn.execute(
        """INSERT INTO reports (case_id, content_stamp, body, ai_sections_json, status, created_by)
           VALUES (?, ?, ?, ?, 'draft', ?)""",
        (case_id, result["content_stamp"], result["full_text"], json.dumps(result["sections"]), user["id"]),
    )
    conn.commit()
    audit.record("report.generated", item_id=str(cur.lastrowid), case_ref=case["ref"])

    return {"report_id": cur.lastrowid, "body": result["full_text"], "sections": result["sections"]}


class EditReportBody(BaseModel):
    body: str


@app.patch("/api/reports/{report_id}")
def edit_report(report_id: int, body: EditReportBody, request: Request):
    identity.require_identity(request)
    conn = storage.get_conn()
    report = conn.execute("SELECT status FROM reports WHERE id = ?", (report_id,)).fetchone()
    if report is None:
        raise HTTPException(404, "Report not found.")
    if report["status"] not in ("draft", "returned"):
        raise HTTPException(409, f"Report is locked (status: {report['status']}) and cannot be edited.")

    conn.execute("UPDATE reports SET body = ? WHERE id = ?", (body.body, report_id))
    conn.commit()
    return {"ok": True}


@app.get("/api/cases/{case_id}/checks")
async def get_checks(case_id: int, request: Request):
    identity.require_identity(request)
    conn = storage.get_conn()
    case = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    if case is None:
        raise HTTPException(404, "Case not found.")
    report = conn.execute(
        "SELECT body FROM reports WHERE case_id = ? ORDER BY id DESC LIMIT 1", (case_id,)
    ).fetchone()
    return await checks.run_all_checks(case_id, report["body"] if report else None)


class SubmitReportBody(BaseModel):
    typed_name: str
    statements_ticked: list[bool]


@app.post("/api/reports/{report_id}/submit")
async def submit_report(report_id: int, body: SubmitReportBody, request: Request):
    ident = identity.require_identity(request)
    conn = storage.get_conn()
    rep = conn.execute("SELECT case_id FROM reports WHERE id = ?", (report_id,)).fetchone()
    checks_snapshot = await checks.run_all_checks(rep["case_id"]) if rep else None
    try:
        review.submit_for_review(report_id, ident, body.typed_name, body.statements_ticked, checks_snapshot)
    except review.ReviewError as e:
        raise HTTPException(422, str(e))
    return {"ok": True}


@app.post("/api/reports/{report_id}/approve")
def approve_report_route(report_id: int, request: Request):
    ident = identity.require_role(request, "supervisor")
    user = storage.get_user_by_principal(ident.principal)
    try:
        review.approve_report(report_id, ident, user["display_name"])
    except review.ReviewError as e:
        raise HTTPException(422, str(e))
    return {"ok": True}


class ReturnReportBody(BaseModel):
    message: str


@app.post("/api/reports/{report_id}/return")
def return_report_route(report_id: int, body: ReturnReportBody, request: Request):
    ident = identity.require_role(request, "supervisor")
    try:
        review.return_report(report_id, ident, body.message)
    except review.ReviewError as e:
        raise HTTPException(422, str(e))
    return {"ok": True}


@app.get("/api/reports/{report_id}")
def get_report(report_id: int, request: Request):
    identity.require_identity(request)
    conn = storage.get_conn()
    report = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if report is None:
        raise HTTPException(404, "Report not found.")
    stale = report_module.is_stale(report["content_stamp"], report["case_id"])
    return {**dict(report), "stale": stale}


# ---------------------------------------------------------------------------
# Dashboard / PIRM
# ---------------------------------------------------------------------------

@app.get("/api/cases/{case_id}/dashboard")
def case_dashboard(case_id: int, request: Request):
    identity.require_identity(request)
    return {
        "tasks": dashboard.tasks_for_case(case_id),
        "progress": dashboard.progress_for_case(case_id),
    }


@app.get("/api/subjects/{subject_id}/pirm")
def subject_pirm(subject_id: int, request: Request):
    identity.require_identity(request)
    return dashboard.compute_pirm(subject_id)


# ---------------------------------------------------------------------------
# Audit (admin-only export and human history view)
# ---------------------------------------------------------------------------

HUB_STAGE_INDEX = {s: i for i, s in enumerate(taxonomy.HUB_STAGES)}


class CreateHubTaskingBody(BaseModel):
    source: str
    subject_code: str | None = None
    task_description: str
    priority: str
    due_date: str


@app.post("/api/hub-taskings")
def create_hub_tasking(body: CreateHubTaskingBody, request: Request):
    identity.require_identity(request)
    taxonomy.validate(body.priority, taxonomy.ACTION_PRIORITIES, "priority")
    subject_id = storage.get_or_create_subject(body.subject_code) if body.subject_code else None
    ref = _next_ref("Hub", "hub_taskings")

    conn = storage.get_conn()
    cur = conn.execute(
        """INSERT INTO hub_taskings (ref, source, subject_id, task_description, priority, due_date, stage_index)
           VALUES (?, ?, ?, ?, ?, ?, 0)""",
        (ref, body.source, subject_id, body.task_description, body.priority, body.due_date),
    )
    conn.commit()
    audit.record("hub_tasking.created", item_id=str(cur.lastrowid))
    return {"id": cur.lastrowid, "ref": ref}


@app.get("/api/hub-taskings")
def list_hub_taskings(request: Request):
    identity.require_identity(request)
    conn = storage.get_conn()
    rows = conn.execute(
        """SELECT hub_taskings.*, subjects.code AS subject_code, u.display_name AS allocated_officer_name,
                  u.principal AS allocated_officer_principal
           FROM hub_taskings
           LEFT JOIN subjects ON subjects.id = hub_taskings.subject_id
           LEFT JOIN users u ON u.id = hub_taskings.allocated_officer_id
           ORDER BY hub_taskings.id DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/hub-taskings/{tasking_id}")
def get_hub_tasking(tasking_id: int, request: Request):
    identity.require_identity(request)
    conn = storage.get_conn()
    row = conn.execute(
        """SELECT hub_taskings.*, subjects.code AS subject_code, u.display_name AS allocated_officer_name,
                  u.principal AS allocated_officer_principal
           FROM hub_taskings
           LEFT JOIN subjects ON subjects.id = hub_taskings.subject_id
           LEFT JOIN users u ON u.id = hub_taskings.allocated_officer_id
           WHERE hub_taskings.id = ?""",
        (tasking_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Hub tasking not found.")
    return dict(row)


class ReassignHubOfficerBody(BaseModel):
    officer_principal: str | None


@app.post("/api/hub-taskings/{tasking_id}/officer")
def reassign_hub_officer(tasking_id: int, body: ReassignHubOfficerBody, request: Request):
    identity.require_role(request, "supervisor")
    conn = storage.get_conn()
    tasking = conn.execute("SELECT id, stage_index FROM hub_taskings WHERE id = ?", (tasking_id,)).fetchone()
    if tasking is None:
        raise HTTPException(404, "Hub tasking not found.")

    officer_id = None
    new_stage = tasking["stage_index"]
    if body.officer_principal:
        user = storage.get_user_by_principal(body.officer_principal)
        if not user:
            raise HTTPException(404, "Officer not found.")
        officer_id = user["id"]
        if new_stage == HUB_STAGE_INDEX["Received"]:
            new_stage = HUB_STAGE_INDEX["Allocated"]
    else:
        new_stage = HUB_STAGE_INDEX["Received"]  # unallocating returns it to Received

    conn.execute(
        "UPDATE hub_taskings SET allocated_officer_id = ?, stage_index = ? WHERE id = ?",
        (officer_id, new_stage, tasking_id),
    )
    conn.commit()
    audit.record("hub_tasking.officer_reassigned", item_id=str(tasking_id))
    return {"ok": True}


class HubTaskingStageBody(BaseModel):
    stage: str


@app.post("/api/hub-taskings/{tasking_id}/stage")
def set_hub_tasking_stage(tasking_id: int, body: HubTaskingStageBody, request: Request):
    identity.require_identity(request)
    taxonomy.validate(body.stage, taxonomy.HUB_STAGES, "hub tasking stage")
    conn = storage.get_conn()
    tasking = conn.execute(
        "SELECT allocated_officer_id FROM hub_taskings WHERE id = ?", (tasking_id,)
    ).fetchone()
    if tasking is None:
        raise HTTPException(404, "Hub tasking not found.")
    stage_index = HUB_STAGE_INDEX[body.stage]
    if stage_index > 0 and tasking["allocated_officer_id"] is None:
        raise HTTPException(422, "Cannot move a tasking past 'Received' with no officer allocated.")

    conn.execute("UPDATE hub_taskings SET stage_index = ? WHERE id = ?", (stage_index, tasking_id))
    conn.commit()
    audit.record("hub_tasking.stage_changed", item_id=str(tasking_id), detail={"stage": body.stage})
    return {"ok": True}


class CreateNiaBody(BaseModel):
    subject_code: str
    status: str
    opened_at: str
    next_review: str
    summary: str
    detail: str = ""


@app.post("/api/nia")
def create_nia(body: CreateNiaBody, request: Request):
    identity.require_identity(request)
    taxonomy.validate(body.status, taxonomy.NIA_STATUSES, "NIA status")
    subject_id = storage.get_or_create_subject(body.subject_code)
    ref = _next_ref("NIA", "nia_records")
    conn = storage.get_conn()
    cur = conn.execute(
        "INSERT INTO nia_records (ref, subject_id, status, opened_at, next_review, summary, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ref, subject_id, body.status, body.opened_at, body.next_review, body.summary, body.detail),
    )
    conn.commit()
    audit.record("nia.created", item_id=str(cur.lastrowid))
    return {"id": cur.lastrowid, "ref": ref}


@app.get("/api/nia")
def list_nia(request: Request):
    identity.require_identity(request)
    conn = storage.get_conn()
    rows = conn.execute(
        """SELECT nia_records.*, subjects.code AS subject_code FROM nia_records
           JOIN subjects ON subjects.id = nia_records.subject_id ORDER BY nia_records.id DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


class CreateBiBody(BaseModel):
    subject_code: str
    status: str
    opened_at: str
    next_review: str
    summary: str
    detail: str = ""


@app.post("/api/business-interests")
def create_bi(body: CreateBiBody, request: Request):
    identity.require_identity(request)
    taxonomy.validate(body.status, taxonomy.BI_STATUSES, "business interest status")
    subject_id = storage.get_or_create_subject(body.subject_code)
    ref = _next_ref("BI", "business_interests")
    conn = storage.get_conn()
    cur = conn.execute(
        "INSERT INTO business_interests (ref, subject_id, status, opened_at, next_review, summary, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ref, subject_id, body.status, body.opened_at, body.next_review, body.summary, body.detail),
    )
    conn.commit()
    audit.record("business_interest.created", item_id=str(cur.lastrowid))
    return {"id": cur.lastrowid, "ref": ref}


@app.get("/api/business-interests")
def list_bi(request: Request):
    identity.require_identity(request)
    conn = storage.get_conn()
    rows = conn.execute(
        """SELECT business_interests.*, subjects.code AS subject_code FROM business_interests
           JOIN subjects ON subjects.id = business_interests.subject_id ORDER BY business_interests.id DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/pirm")
def list_pirm(request: Request):
    identity.require_identity(request)
    conn = storage.get_conn()
    subject_ids = [r["id"] for r in conn.execute("SELECT id FROM subjects").fetchall()]
    scored = [dict(subject_id=sid, subject_code=conn.execute(
        "SELECT code FROM subjects WHERE id = ?", (sid,)).fetchone()["code"], **dashboard.compute_pirm(sid))
        for sid in subject_ids]
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


@app.get("/api/subjects/{subject_id}")
def get_subject(subject_id: int, request: Request):
    """Master subject record: everything linked to this person, in one place."""
    identity.require_identity(request)
    conn = storage.get_conn()
    subject = conn.execute("SELECT * FROM subjects WHERE id = ?", (subject_id,)).fetchone()
    if subject is None:
        raise HTTPException(404, "Subject not found.")

    cases_rows = conn.execute(
        """SELECT cases.id, cases.ref, cases.stage_index, cases.risk_impact, cases.risk_likelihood,
                  u.display_name AS officer_name
           FROM cases LEFT JOIN users u ON u.id = cases.officer_id
           WHERE subject_id = ?""",
        (subject_id,),
    ).fetchall()
    hub_rows = conn.execute("SELECT id, ref, stage_index FROM hub_taskings WHERE subject_id = ?", (subject_id,)).fetchall()
    nia_rows = conn.execute("SELECT id, ref, status FROM nia_records WHERE subject_id = ?", (subject_id,)).fetchall()
    bi_rows = conn.execute("SELECT id, ref, status FROM business_interests WHERE subject_id = ?", (subject_id,)).fetchall()

    cases = [dict(r) for r in cases_rows]
    for c in cases:
        c["stage_index"] = dashboard.compute_stage_index(conn, c["id"])

    return {
        "subject": dict(subject),
        "cases": cases,
        "hub_taskings": [dict(r) for r in hub_rows],
        "nia": [dict(r) for r in nia_rows],
        "business_interests": [dict(r) for r in bi_rows],
        "pirm": dashboard.compute_pirm(subject_id),
    }


@app.get("/api/audit/history")
def audit_history(request: Request, case_ref: str | None = None):
    identity.require_role(request, "admin")
    conn = storage.get_conn()
    if case_ref:
        rows = conn.execute("SELECT * FROM audit_log WHERE case_ref = ? ORDER BY id", (case_ref,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id").fetchall()
    return audit.render_history(rows)


@app.get("/api/audit/verify")
def audit_verify(request: Request):
    identity.require_role(request, "admin")
    ok, broken_id = audit.verify_chain()
    return {"ok": ok, "broken_at": broken_id}


# ---------------------------------------------------------------------------
# Frontend -- vanilla HTML/CSS/JS, served same-origin, no build step, no CDN.
# Mounted last so it never shadows an /api/* route above.
# ---------------------------------------------------------------------------
from pathlib import Path
from fastapi.staticfiles import StaticFiles

_FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
if _FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
