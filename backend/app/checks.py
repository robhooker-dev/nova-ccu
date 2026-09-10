"""
The second reader, run before submission: deterministic checks always;
an AI check when live. It never blocks and never decides -- but submission
requires the check to have been run for the current version, and any
outstanding findings are appended to what the officer attests to.
"""
from . import llm, storage


def run_deterministic_checks(case_id: int) -> list[dict]:
    """Structure, staleness-adjacent facts, decisions recorded, citations --
    the Nova-CCU equivalents of Nova-PSD's second reader. Each finding is
    {check, passed, message}."""
    conn = storage.get_conn()
    case = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
    findings = []

    findings.append({
        "check": "Officer allocated",
        "passed": case["officer_id"] is not None,
        "message": "No officer in charge is allocated to this case." if case["officer_id"] is None else "",
    })

    intel = conn.execute("SELECT 1 FROM intelligence WHERE case_id = ?", (case_id,)).fetchone()
    findings.append({
        "check": "Intelligence record complete",
        "passed": intel is not None,
        "message": "No intelligence record is attached to this case." if intel is None else "",
    })

    risk_done = case["risk_rationale"] not in ("", "Risk assessment not yet completed.")
    findings.append({
        "check": "Risk assessment completed",
        "passed": risk_done,
        "message": "Risk assessment has not been completed for this case." if not risk_done else "",
    })

    adc = conn.execute("SELECT 1 FROM adc_decisions WHERE case_id = ?", (case_id,)).fetchone()
    from . import dashboard, taxonomy
    past_adc_stage = dashboard.compute_stage_index(conn, case_id, case) >= taxonomy.STAGES.index("ADC Complete, awaiting allocation")
    findings.append({
        "check": "ADC decision recorded",
        "passed": (adc is not None) or not past_adc_stage,
        "message": "This case is past the ADC stage but has no recorded decision." if (adc is None and past_adc_stage) else "",
    })

    open_high_priority = conn.execute(
        "SELECT COUNT(*) AS n FROM actions WHERE case_id = ? AND status != 'Complete' AND priority = 'High'",
        (case_id,),
    ).fetchone()["n"]
    findings.append({
        "check": "No open high-priority actions",
        "passed": open_high_priority == 0,
        "message": f"{open_high_priority} high-priority action(s) are still open." if open_high_priority else "",
    })

    updates = conn.execute("SELECT COUNT(*) AS n FROM case_updates WHERE case_id = ?", (case_id,)).fetchone()["n"]
    findings.append({
        "check": "At least one case update recorded",
        "passed": updates > 0,
        "message": "No case updates have been recorded -- there is no officer narrative to report against." if updates == 0 else "",
    })

    return findings


async def run_ai_check(report_body: str) -> str | None:
    """
    Only runs when the LLM is live (mock mode returns None -- an AI check
    against placeholder text would itself be placeholder noise, which is
    worse than no check at all).
    """
    if llm.mode() != "live":
        return None

    prompt = (
        "You are a second reader for a UK police Counter Corruption Unit report. "
        "Read the report below. Does the 'Conclusion and recommendations' section "
        "actually follow from the 'Intelligence' and 'Research and Actions completed' "
        "sections, or does it assert something the earlier sections don't support? "
        "Answer in one or two sentences. If it looks consistent, say so plainly rather "
        "than inventing a concern.\n\nREPORT:\n" + report_body
    )
    result = await llm.chat(prompt, max_output_tokens=200)
    return result.text


async def run_all_checks(case_id: int, report_body: str | None = None) -> dict:
    deterministic = run_deterministic_checks(case_id)
    ai_note = await run_ai_check(report_body) if report_body else None
    return {
        "deterministic": deterministic,
        "ai_note": ai_note,
        "all_passed": all(f["passed"] for f in deterministic),
    }
