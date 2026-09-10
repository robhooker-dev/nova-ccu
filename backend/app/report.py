"""
Report builder: a list of section specs, each with a `kind` that becomes its
stored provenance. One LLM call per section, never one giant call -- gives
per-section regenerate, isolates failures, keeps each prompt small, and lets
a failed section say so in the document rather than vanishing.

provenance:
  system       Deterministic template -- title, OIC, date. Never LLM output.
  investigator Verbatim from the human -- officer updates, recorded
               recommendations. Never rewritten by the model.
  ai           Drafted, one LLM call per section. Regenerate or edit.

A content_stamp hashes the inputs a report was built from. If those inputs
change afterwards, the report is stale -- needs regenerating. Silent
staleness is how wrong documents get signed.
"""
import hashlib
import json
from datetime import datetime, timezone

from . import llm, storage


def _case_facts(case_id: int) -> dict:
    conn = storage.get_conn()
    case = conn.execute(
        """SELECT cases.*, subjects.code AS subject_code, u.display_name AS officer_name
           FROM cases JOIN subjects ON subjects.id = cases.subject_id
           LEFT JOIN users u ON u.id = cases.officer_id
           WHERE cases.id = ?""",
        (case_id,),
    ).fetchone()
    intel = conn.execute("SELECT * FROM intelligence WHERE case_id = ?", (case_id,)).fetchone()
    adc = conn.execute("SELECT * FROM adc_decisions WHERE case_id = ?", (case_id,)).fetchone()
    investigation = conn.execute("SELECT * FROM investigations WHERE case_id = ?", (case_id,)).fetchone()
    actions = conn.execute("SELECT * FROM actions WHERE case_id = ? ORDER BY id", (case_id,)).fetchall()
    updates = conn.execute(
        """SELECT case_updates.*, u.display_name AS entered_by_name
           FROM case_updates JOIN users u ON u.id = case_updates.entered_by
           WHERE case_id = ? ORDER BY created_at""",
        (case_id,),
    ).fetchall()

    # Only the case fields the report body actually renders (see
    # _facts_to_plain_text below) go into content_stamp. Hashing the whole
    # raw row previously pulled in administrative bookkeeping columns --
    # stage_index, updated_at -- that never appear in the report text but
    # do change independently of it, which flagged reports stale with no
    # actual change to their content (e.g. immediately after submission,
    # when stage_index used to be rewritten in the same request).
    case_fields = {
        "ref": case["ref"], "subject_code": case["subject_code"], "officer_name": case["officer_name"],
        "risk_impact": case["risk_impact"], "risk_likelihood": case["risk_likelihood"],
        "risk_rationale": case["risk_rationale"],
    } if case else {}

    return {
        "case": case_fields,
        "intel": dict(intel) if intel else {},
        "adc": dict(adc) if adc else None,
        "investigation": dict(investigation) if investigation else None,
        "actions": [dict(a) for a in actions],
        "updates": [dict(u) for u in updates],
    }


def content_stamp(facts: dict) -> str:
    """Hash of the inputs a report was built from -- used to detect staleness."""
    canon = json.dumps(facts, sort_keys=True, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _facts_to_plain_text(facts: dict) -> str:
    lines = []
    c = facts["case"]
    lines.append("CASE SUMMARY")
    lines.append(f"Reference: {c.get('ref')}")
    lines.append(f"Subject: {c.get('subject_code')}")
    lines.append(f"Officer in charge: {c.get('officer_name') or 'OIC TBC'}")
    lines.append(f"Risk: impact {c.get('risk_impact')} x likelihood {c.get('risk_likelihood')}")
    lines.append(f"Risk rationale: {c.get('risk_rationale')}")
    lines.append("")

    i = facts["intel"]
    if i:
        lines.append("INTELLIGENCE")
        lines.append(f"URN: {i.get('urn')}")
        lines.append(f"Source type: {i.get('source_type')}")
        lines.append(f"Grading: {i.get('source_evaluation')}/{i.get('intelligence_evaluation')}/{i.get('handling_code')}")
        lines.append(f"Summary: {i.get('summary')}")
        lines.append("")

    if facts["adc"]:
        a = facts["adc"]
        lines.append("ADC DECISION")
        lines.append(f"Decision: {a.get('decision')}")
        lines.append(f"Rationale: {a.get('rationale')}")
        lines.append("")

    if facts["actions"]:
        lines.append("ACTIONS")
        for a in facts["actions"]:
            lines.append(f"[{a['status']}] {a['title']} (due {a['due_date']})")
        lines.append("")

    if facts["updates"]:
        lines.append("OFFICER UPDATES")
        for u in facts["updates"]:
            lines.append(f"{u['created_at']} \u2014 {u['entered_by_name']} ({u['entered_by_role_label']}): {u['text']}")
        lines.append("")

    return "\n".join(lines)


AI_PROMPT = (
    "You are drafting a final case report for a UK police Counter Corruption Unit, "
    "for a supervisor's review. Using ONLY the facts provided below, write exactly "
    "three sections, using exactly these headings and nothing else -- do not add a "
    "title, do not repeat the OIC or Date, do not add any other heading:\n\n"
    "Intelligence -\n\nResearch and Actions completed -\n\nConclusion and recommendations -\n\n"
    "Under 'Intelligence', summarise the intelligence received, its source and grading, in prose. "
    "Under 'Research and Actions completed', narrate what has been done on the case so far. "
    "Under 'Conclusion and recommendations', summarise the current risk position and any ADC "
    "decision, then set out recommendations for next steps. The OIC's recommendations live "
    "inside the OFFICER UPDATES entries below, not in a separate field -- treat any "
    "recommendation stated in an officer update as the primary basis for this section, and "
    "reflect it explicitly. If no update contains a recommendation, say that no recommendation "
    "has yet been recorded by the OIC, rather than inventing one. Write in flowing prose "
    "paragraphs, not bullet points. Do not invent, assume, or add any fact not present below.\n\n"
    "FACTS:\n{facts}"
)


async def generate_report(case_id: int) -> dict:
    facts = _case_facts(case_id)
    stamp = content_stamp(facts)
    plain_facts = _facts_to_plain_text(facts)

    result = await llm.chat(AI_PROMPT.format(facts=plain_facts), max_output_tokens=1000)

    c = facts["case"]
    header = (
        f"{c.get('ref')} Final Report\n"
        f"OIC: {c.get('officer_name') or 'OIC TBC'}\n"
        f"Date: {datetime.now(timezone.utc).strftime('%d %B %Y')}"
    )

    sections = {
        "header": {"kind": "system", "text": header},
        "ai_body": {"kind": "ai", "text": result.text, "llm_mode": result.mode_used, "finish_reason": result.finish_reason},
    }

    full_text = header + "\n\n" + result.text

    return {
        "content_stamp": stamp,
        "sections": sections,
        "full_text": full_text,
        "facts": facts,
    }


def is_stale(stored_stamp: str, case_id: int) -> bool:
    current = content_stamp(_case_facts(case_id))
    return current != stored_stamp


RISK_RATIONALE_EXAMPLES = """\
Example 1:
"Information had been received identifying that PC **** arrested Josh Berwick, the nephew of his partner DC ****. The victims in the investigation are reported to be her parents and brother. The circumstances create an apparent conflict of interest and raise concerns regarding the management of familial relationships, investigative independence and potential access to police information. No evidence of misconduct has been identified at this stage; however, proportionate intelligence development is recommended to establish whether police powers, systems, or information have been utilised inappropriately and whether relevant conflict management processes were followed."

Example 2:
"The allegations indicate potential concerns relating to standards of professional behaviour, misuse of police status and inappropriate associations. The reported friendship with an individual who is the subject of an ongoing rape investigation is particularly concerning and if identified will require an NIA assessment."

Example 3:
"The intelligence presents allegations of potential misconduct relating to the misuse of working time, inappropriate use of police systems and information, inaccurate recording of working hours, and workplace culture within the MI Team. If substantiated, the matters have the potential to impact organisational integrity, information security, supervisory oversight and public confidence; however, the information remains uncorroborated. The rationale for recording is to ensure the information is appropriately captured and disseminated to Reactive PSD who retain ownership. There is no current requirement for CCU enquiries at this stage. The sanitised intelligence has now been forwarded to them for any proportionate action deemed necessary."
"""

RISK_RATIONALE_PROMPT = (
    "You are drafting the rationale for a risk assessment on a UK police Counter Corruption Unit case. "
    "Write ONE paragraph in the house style shown in the examples below -- match their register, structure, "
    "and level of hedging exactly, but do not copy their wording or specific facts; base the content entirely "
    "on the case facts provided.\n\n"
    "The rationale must, in this rough order:\n"
    "1. Briefly state the circumstances giving rise to the concern, drawn from the intelligence.\n"
    "2. Name the specific standards or concern categories raised (e.g. conflict of interest, misuse of "
    "position, inappropriate association, misuse of systems, standards of professional behaviour) -- only "
    "ones actually supported by the facts given, do not list categories that don't apply.\n"
    "3. Explicitly address whether there is currently evidence of misconduct and/or a criminal offence, using "
    "appropriately hedged language given the stated evaluation and grading of the intelligence (e.g. 'no "
    "evidence of misconduct has been identified at this stage', 'presents allegations of potential "
    "misconduct', 'if substantiated' -- calibrate the hedge to how corroborated the intelligence actually is).\n"
    "4. Explicitly address whether, if the concerns were substantiated, this would impact public trust and "
    "confidence and/or organisational integrity -- only make this connection where the facts genuinely support it.\n"
    "5. State a proportionate recommended next step consistent with the stated impact and likelihood ratings "
    "(e.g. intelligence development, NIA assessment, referral to another department, no current requirement "
    "for further CCU action).\n\n"
    "Do not invent facts, names, ranks, or details not present below. Do not use placeholder names like 'PC "
    "****' unless the case facts genuinely withhold a name -- refer to the subject by the code given. Write "
    "flowing prose, not a bulleted list, in a single paragraph unless the facts clearly call for two.\n\n"
    "HOUSE STYLE EXAMPLES (style only -- do not reuse their facts or wording):\n"
    f"{RISK_RATIONALE_EXAMPLES}\n"
    "CASE FACTS:\n{facts}\n\n"
    "STATED IMPACT: {impact_label}\n"
    "STATED LIKELIHOOD: {likelihood_label}"
)


async def generate_risk_rationale(case_id: int, impact: int, likelihood: int) -> str:
    facts = _case_facts(case_id)
    plain_facts = _facts_to_plain_text(facts)

    from . import taxonomy

    prompt = RISK_RATIONALE_PROMPT.format(
        facts=plain_facts,
        impact_label=taxonomy.IMPACT_LABELS[impact - 1],
        likelihood_label=taxonomy.LIKELIHOOD_LABELS[likelihood - 1],
    )
    result = await llm.chat(prompt, max_output_tokens=350)
    return result.text
