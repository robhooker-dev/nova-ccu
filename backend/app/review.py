"""
Submission and sign-off -- the end of the process. This is what turns a
drafting tool into a system of record.

1. Officer with a current report presses "Send to supervisor".
2. An AI warning and five attestation statements are shown; all must be
   ticked. The statements are stored verbatim with the submission.
3. Typed name must match the signed-in identity.
4. The report locks (status = submitted) so signed content cannot drift.
5. Supervisor either approves (their own attestation) or returns it with a
   message. Nobody can sign off their own submission.

Every step is audited: submitted / returned / approved.
"""
from . import audit, report as report_module, storage

ATTESTATION_STATEMENTS = [
    "I have read the full report, including every AI-drafted section.",
    "I have checked the AI-drafted sections against the case record and "
    "corrected anything inaccurate or unsupported.",
    "The recommendation reflects my own professional judgement, not the "
    "AI's suggestion, where the two differ.",
    "I understand this report may be reviewed and relied upon by a "
    "supervisor and, potentially, in later proceedings.",
    "I am satisfied this report is ready for supervisor review.",
]


class ReviewError(Exception):
    pass


def submit_for_review(
    report_id: int, identity, typed_name: str, statements_ticked: list[bool], checks_snapshot: dict | None = None
) -> None:
    if typed_name.strip().lower() != identity.display_name.strip().lower():
        raise ReviewError("Typed name does not match the signed-in identity.")
    if len(statements_ticked) != len(ATTESTATION_STATEMENTS) or not all(statements_ticked):
        raise ReviewError("All attestation statements must be confirmed before submission.")

    conn = storage.get_conn()
    report = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if report is None:
        raise ReviewError("Report not found.")
    if report["status"] not in ("draft", "returned"):
        raise ReviewError(f"Report cannot be submitted from status '{report['status']}'.")

    if report_module.is_stale(report["content_stamp"], report["case_id"]):
        raise ReviewError("Report is stale -- the case has changed since this report was built. Regenerate first.")

    import json

    user = storage.get_user_by_principal(identity.principal)
    payload = {"statements": ATTESTATION_STATEMENTS, "checks_at_submission": checks_snapshot}

    with storage.tx() as c:
        c.execute("UPDATE reports SET status = 'submitted' WHERE id = ?", (report_id,))
        c.execute(
            """INSERT INTO attestations
               (report_id, statements_json, typed_name, signed_by, content_stamp_at_signing)
               VALUES (?, ?, ?, ?, ?)""",
            (report_id, json.dumps(payload), typed_name, user["id"], report["content_stamp"]),
        )

    case_ref = conn.execute("SELECT ref FROM cases WHERE id = ?", (report["case_id"],)).fetchone()["ref"]
    audit.record("report.attested", item_id=str(report_id), case_ref=case_ref)
    audit.record("report.submitted", item_id=str(report_id), case_ref=case_ref)
    # The case's administrative stage ("With DS for review", "Case
    # Finalised", ...) is derived live from report/ADC/officer state --
    # see dashboard.compute_stage_index -- so nothing needs writing here.


def approve_report(report_id: int, identity, typed_name: str) -> None:
    conn = storage.get_conn()
    report = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if report is None:
        raise ReviewError("Report not found.")
    if report["status"] != "submitted":
        raise ReviewError("Only a submitted report can be approved.")

    submitter = conn.execute(
        "SELECT signed_by FROM attestations WHERE report_id = ? ORDER BY id DESC LIMIT 1", (report_id,)
    ).fetchone()
    approver = storage.get_user_by_principal(identity.principal)
    if submitter and submitter["signed_by"] == approver["id"]:
        raise ReviewError("You cannot approve a report you submitted yourself.")

    conn.execute("UPDATE reports SET status = 'approved' WHERE id = ?", (report_id,))
    conn.commit()

    case_ref = conn.execute("SELECT ref FROM cases WHERE id = ?", (report["case_id"],)).fetchone()["ref"]
    audit.record("report.approved", item_id=str(report_id), case_ref=case_ref)


def return_report(report_id: int, identity, message: str) -> None:
    conn = storage.get_conn()
    report = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if report is None:
        raise ReviewError("Report not found.")
    if report["status"] != "submitted":
        raise ReviewError("Only a submitted report can be returned.")

    conn.execute("UPDATE reports SET status = 'returned' WHERE id = ?", (report_id,))
    conn.commit()

    case_ref = conn.execute("SELECT ref FROM cases WHERE id = ?", (report["case_id"],)).fetchone()["ref"]
    audit.record("report.returned", item_id=str(report_id), case_ref=case_ref, detail={"message": message})
