"""
Derived state, not stored state. Computed live from the record every time.

Because nothing here is stored, it survives handovers automatically -- hand
a case to another officer and their task list is simply correct, with no
migration or backfill needed.
"""
from . import storage, taxonomy


def compute_stage_index(conn, case_id: int, case=None) -> int:
    """
    The case's administrative stage, derived live from what has actually
    happened -- same rule as tasks_for_case/progress_for_case below. This
    used to be a stored column written only at case creation and again on
    report submission, so an ADC decision, an officer allocation or a
    report approval never advanced it -- a case could be fully investigated
    and still display "File created". Deriving it removes that whole class
    of missed-update bug.
    """
    if case is None:
        case = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    if case is None:
        return 0

    latest_report = conn.execute(
        "SELECT status FROM reports WHERE case_id = ? ORDER BY id DESC LIMIT 1", (case_id,)
    ).fetchone()
    if latest_report and latest_report["status"] == "approved":
        return taxonomy.STAGES.index("Case Finalised")
    if latest_report and latest_report["status"] == "submitted":
        return taxonomy.STAGES.index("With DS for review")

    adc = conn.execute("SELECT 1 FROM adc_decisions WHERE case_id = ?", (case_id,)).fetchone()
    if adc is None:
        return taxonomy.STAGES.index("File created")
    if case["officer_id"] is None:
        return taxonomy.STAGES.index("ADC Complete, awaiting allocation")
    return taxonomy.STAGES.index("In progress")


def tasks_for_case(case_id: int) -> list[dict]:
    conn = storage.get_conn()
    case = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    if case is None:
        return []

    tasks = []

    if case["officer_id"] is None:
        tasks.append({"type": "decide", "label": "Allocate an officer in charge", "tab": "case"})

    intel = conn.execute("SELECT * FROM intelligence WHERE case_id = ?", (case_id,)).fetchone()
    if intel is None:
        tasks.append({"type": "do", "label": "Complete the intelligence record", "tab": "intelligence"})

    if case["risk_rationale"] in ("", "Risk assessment not yet completed."):
        tasks.append({"type": "do", "label": "Complete the risk assessment", "tab": "risk"})

    adc = conn.execute("SELECT * FROM adc_decisions WHERE case_id = ?", (case_id,)).fetchone()
    if adc is None and compute_stage_index(conn, case_id, case) >= taxonomy.STAGES.index("ADC Complete, awaiting allocation"):
        tasks.append({"type": "decide", "label": "Record the ADC decision", "tab": "adc"})

    open_actions = conn.execute(
        "SELECT COUNT(*) AS n FROM actions WHERE case_id = ? AND status != 'Complete'", (case_id,)
    ).fetchone()["n"]
    if open_actions > 0:
        tasks.append({"type": "do", "label": f"{open_actions} action(s) still open", "tab": "investigation"})

    report = conn.execute(
        "SELECT * FROM reports WHERE case_id = ? ORDER BY id DESC LIMIT 1", (case_id,)
    ).fetchone()
    if report and report["status"] == "returned":
        tasks.append({"type": "urgent", "label": "Report was returned by supervisor -- needs changes", "tab": "investigation"})

    return tasks


def progress_for_case(case_id: int) -> dict:
    conn = storage.get_conn()
    case = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    if case is None:
        return {"percent": 0, "stage_label": "Unknown", "last_activity": None}

    checklist = [
        case["officer_id"] is not None,
        conn.execute("SELECT 1 FROM intelligence WHERE case_id = ?", (case_id,)).fetchone() is not None,
        case["risk_rationale"] not in ("", "Risk assessment not yet completed."),
        conn.execute("SELECT 1 FROM adc_decisions WHERE case_id = ?", (case_id,)).fetchone() is not None,
        conn.execute(
            "SELECT 1 FROM actions WHERE case_id = ? AND status != 'Complete'", (case_id,)
        ).fetchone() is None,
        conn.execute(
            "SELECT 1 FROM reports WHERE case_id = ? AND status = 'approved'", (case_id,)
        ).fetchone() is not None,
    ]
    percent = round(100 * sum(1 for c in checklist if c) / len(checklist))

    last = conn.execute(
        """SELECT MAX(ts) AS last_ts FROM audit_log WHERE case_ref = ?""",
        (case["ref"],),
    ).fetchone()

    return {
        "percent": percent,
        "stage_label": taxonomy.STAGES[compute_stage_index(conn, case_id, case)],
        "last_activity": last["last_ts"] if last else None,
    }


def compute_pirm(subject_id: int) -> dict:
    """
    PIRM score, computed live from every linked record -- never manually
    entered. This mirrors the scoring already validated in the prototype:
    weighted by case risk band, NIA/BI status, and Hub Tasking count.
    """
    conn = storage.get_conn()
    factors = []
    score = 0

    case = conn.execute(
        "SELECT ref, risk_impact, risk_likelihood, stage_index FROM cases WHERE subject_id = ?", (subject_id,)
    ).fetchone()
    if case:
        band = taxonomy.risk_band_from_score(case["risk_impact"], case["risk_likelihood"])
        weight = {"High": 30, "Medium": 15, "Low": 5}[band]
        score += weight
        factors.append({"label": f"CCU case {case['ref']}", "detail": f"{band} risk", "points": weight})

    nia = conn.execute("SELECT ref, status FROM nia_records WHERE subject_id = ?", (subject_id,)).fetchone()
    if nia:
        weight = {"Under review": 15, "Managed": 10}.get(nia["status"], 5)
        score += weight
        factors.append({"label": f"NIA {nia['ref']}", "detail": nia["status"], "points": weight})

    bi = conn.execute("SELECT ref, status FROM business_interests WHERE subject_id = ?", (subject_id,)).fetchone()
    if bi:
        weight = 10 if bi["status"] == "Under review" else 5
        score += weight
        factors.append({"label": f"Business interest {bi['ref']}", "detail": bi["status"], "points": weight})

    hub_count = conn.execute(
        "SELECT COUNT(*) AS n FROM hub_taskings WHERE subject_id = ?", (subject_id,)
    ).fetchone()["n"]
    if hub_count:
        weight = hub_count * 5
        score += weight
        factors.append({"label": f"{hub_count} Hub tasking(s)", "detail": "", "points": weight})

    band = "High" if score >= 30 else "Medium" if score >= 15 else "Low"
    return {"score": score, "band": band, "factors": factors}
