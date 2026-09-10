"""
Seed entirely fictional training/demo data into a running Nova-CCU server,
over the real HTTP API -- the same convention TESTING.md uses for
verification, so this exercises validation, audit and the taxonomy guards
exactly as a real officer's browser would.

Every subject, officer, scenario and reference number below is invented for
this tool. No real names, real force, real crime reference or real person is
represented. Do not point this at anything but a local/dev server.

Usage:
    cd backend
    venv/Scripts/python seed_training_data.py [base_url]

Requires the server already running (see run.py / README) with dev-fallback
auth (i.e. not ENVIRONMENT=production). Safe to re-run: every call creates
new rows via the normal API, it does not touch the database directly.
"""
import os
import sys

import httpx

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"

# If the target is behind the optional site-password gate (see README
# "Deploying (Render)"), set these two env vars before running -- same
# names the server itself reads, so no credential is hardcoded here.
SITE_AUTH = None
if os.environ.get("NOVA_CCU_SITE_USERNAME") and os.environ.get("NOVA_CCU_SITE_PASSWORD"):
    SITE_AUTH = (os.environ["NOVA_CCU_SITE_USERNAME"], os.environ["NOVA_CCU_SITE_PASSWORD"])

# Known officers auto-seeded by main.py on startup.
DC_MARSH = ("dc.marsh@example.police.uk", "DC J. Marsh")
DI_GRAINGER = ("di.grainger@example.police.uk", "DI Grainger")
INTEL_CELL = ("intelligence.cell@example.police.uk", "Intelligence Cell")

# Known officers auto-seeded by main.py on startup already include a
# supervisor (ds.hooker) -- use that rather than the NOVA_CCU_SUPERVISORS
# env-list address, since a user row already exists for the latter with role
# "officer" and, per the documented precedence gotcha, the env list no
# longer promotes it.
SUPERVISOR = ("ds.hooker@example.police.uk", "DS R. Hooker 341")


def headers(user: tuple[str, str]) -> dict:
    principal, name = user
    return {"X-User-Principal": principal, "X-User-Name": name}


def call(client: httpx.Client, method: str, path: str, user: tuple[str, str], **kw):
    resp = client.request(method, path, headers=headers(user), **kw)
    if resp.status_code >= 400:
        print(f"  ! {method} {path} -> {resp.status_code} {resp.text}")
        resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Scenarios -- fictional, one per counter-corruption theme in taxonomy.py.
# "PC 7734" is deliberately used across a case, a hub tasking, an NIA record
# and a business interest, so the PIRM aggregation has something real to show
# (mirrors the worked example in STATUS.md: case + NIA + BI + hub tasking).
# ---------------------------------------------------------------------------

CASES = [
    dict(
        subject_code="PC 7734",
        source="Internal referral",
        category="Corruption — financial",
        source_evaluation="Reliable",
        intelligence_evaluation="B",
        handling_code="P",
        handling_conditions="None",
        gsc="OFFICIAL-SENSITIVE",
        source_reference="ISR/2026/0143",
        crime_ref="",
        sanitised="",
        review_date="12 March 2027",
        summary=(
            "Force finance flagged during a routine audit that PC 7734 holds an "
            "active, undeclared directorship in a private security company. No "
            "business interest declaration is on file for this officer."
        ),
        # Full walkthrough: allocate -> risk -> actions -> update -> ADC ->
        # report -> submit -> approve.
        full_walkthrough=True,
        risk=dict(impact=4, likelihood=4,
                  rationale="High impact given direct access to intelligence relevant to licensed "
                            "premises the company has contracted with; likelihood high given the "
                            "directorship is already confirmed via Companies House."),
        actions=[
            dict(title="Obtain Companies House filing history for the directorship",
                 owner=DI_GRAINGER, priority="High", due_date="22 Sep 2026"),
            dict(title="Request retrospective business interest declaration from PC 7734",
                 owner=DI_GRAINGER, priority="Medium", due_date="29 Sep 2026"),
        ],
        updates=[
            dict(text="Companies House filing confirms directorship registered 14 months ago, "
                      "never declared. No evidence yet of conflicted duties.", role_label="OIC"),
            dict(text="Recommend CCU investigation given the length of non-disclosure and the "
                      "officer's access to licensing intelligence.", role_label="OIC"),
        ],
        adc=dict(decision="CCU Investigation",
                 rationale="Sustained non-disclosure of a business interest with plausible "
                           "conflict to licensing duties meets the threshold for a full CCU "
                           "investigation rather than local resolution."),
    ),
    dict(
        subject_code="PC 5521",
        source="Anonymous",
        category="Corruption — relationships/associations",
        source_evaluation="Untested",
        intelligence_evaluation="C",
        handling_code="C",
        handling_conditions="S2 — Consult originator before sanitisation",
        gsc="OFFICIAL-SENSITIVE",
        source_reference="ISR/2026/0197",
        crime_ref="",
        sanitised="",
        review_date="30 April 2027",
        summary=(
            "Anonymous caller states PC 5521 is in an ongoing relationship with a woman "
            "associated with a group currently subject to an organised crime investigation, "
            "and that they have been seen together outside duty on several occasions."
        ),
        risk=dict(impact=3, likelihood=4,
                  rationale="Not yet corroborated, but the potential association with an active "
                            "OCG investigation makes this a priority to verify quickly."),
        actions=[
            dict(title="Liaise with the OCG investigation team to check for cross-contamination risk",
                 owner=DC_MARSH, priority="High", due_date="18 Sep 2026"),
        ],
        updates=[
            dict(text="Awaiting response from OCG unit before further action; source remains untested.",
                 role_label="OIC"),
        ],
    ),
    dict(
        subject_code="PC 3390",
        source="Intelligence submission",
        category="Abuse of position",
        source_evaluation="Reliable",
        intelligence_evaluation="B",
        handling_code="P",
        handling_conditions="None",
        gsc="OFFICIAL-SENSITIVE",
        source_reference="ISR/2026/0161",
        crime_ref="",
        sanitised="",
        review_date="19 January 2027",
        summary=(
            "Automated PNC audit shows PC 3390 accessed records relating to a former "
            "partner's new partner on three separate occasions, none linked to a crime "
            "reference or a logged policing purpose."
        ),
        risk=dict(impact=3, likelihood=3,
                  rationale="No evidence of onward disclosure, but repeated access with no "
                            "policing purpose is a clear abuse-of-position pattern."),
        actions=[
            dict(title="Obtain full audit trail of all PNC/records access by PC 3390, last 12 months",
                 owner=DC_MARSH, priority="Medium", due_date="25 Sep 2026"),
        ],
        adc=dict(decision="Record Intel and Forward to PSD",
                 rationale="Pattern is concerning but limited in scope and impact; appropriate for "
                           "local PSD management rather than a CCU investigation at this stage."),
    ),
    dict(
        subject_code="PC 8123",
        source="Internal referral",
        category="Data misuse",
        source_evaluation="Untested",
        intelligence_evaluation="D",
        handling_code="C",
        handling_conditions="A3 — Overt use",
        gsc="OFFICIAL-SENSITIVE",
        source_reference="ISR/2026/0210",
        crime_ref="",
        sanitised="",
        review_date="03 June 2027",
        summary=(
            "A colleague reports observing PC 8123 photograph a custody record on a "
            "personal mobile phone during a night shift. No further detail available."
        ),
    ),
    dict(
        subject_code="DS 2201",
        source="Vetting referral",
        category="Vetting concern",
        source_evaluation="Reliable",
        intelligence_evaluation="B",
        handling_code="P",
        handling_conditions="None",
        gsc="OFFICIAL",
        source_reference="ISR/2026/0175",
        crime_ref="",
        sanitised="",
        review_date="11 November 2026",
        summary=(
            "Renewal vetting submission omits a caution for common assault received eight "
            "years ago, subsequently identified via an ACRO cross-check."
        ),
        risk=dict(impact=2, likelihood=3,
                  rationale="Single historic non-disclosure; no evidence of a wider pattern, but "
                            "vetting integrity requires a formal referral."),
        adc=dict(decision="Vetting Referral",
                 rationale="Non-disclosure on a vetting form is a vetting-unit matter in the first "
                           "instance; CCU investigation not warranted absent further findings."),
    ),
    dict(
        subject_code="PC 1188",
        source="Internal referral",
        category="Substance misuse",
        source_evaluation="Untested",
        intelligence_evaluation="C",
        handling_code="P",
        handling_conditions="None",
        gsc="OFFICIAL-SENSITIVE",
        source_reference="ISR/2026/0223",
        crime_ref="",
        sanitised="",
        review_date="14 August 2027",
        summary=(
            "Shift sergeant reports PC 1188 smelling strongly of cannabis on parade on two "
            "occasions in the last month; colleagues separately describe erratic mood."
        ),
    ),
    dict(
        subject_code="PC 6654",
        source="Hotline report",
        category="Corruption — financial",
        source_evaluation="Reliable",
        intelligence_evaluation="B",
        handling_code="P",
        handling_conditions="None",
        gsc="OFFICIAL-SENSITIVE",
        source_reference="ISR/2026/0188",
        crime_ref="",
        sanitised="",
        review_date="27 February 2027",
        summary=(
            "Financial Investigation Unit flags repeated cash deposits into PC 6654's "
            "personal account shortly after duty, coinciding with shifts covering a licensed "
            "premises subject to ongoing after-hours drinking complaints."
        ),
        adc=dict(decision="CCU Investigation",
                 rationale="Financial pattern directly correlated with duty allocation at the "
                           "premises in question meets the threshold for a full investigation.",
                 leave_unallocated=True),
    ),
]

HUB_TASKINGS = [
    dict(source="Force Control Room", subject_code=None,
         task_description="Review the CCTV retention request from Professional Standards "
                           "relating to custody suite access logs, 14 Aug 2026.",
         priority="Medium", due_date="20 Sep 2026"),
    dict(source="PSD referral", subject_code="PC 7734",
         task_description="Cross-check PC 7734's retrospective business interest declaration "
                           "against Companies House records ahead of the CCU review.",
         priority="High", due_date="24 Sep 2026", allocate=DI_GRAINGER, advance=True),
    dict(source="Anonymous", subject_code="PC 9002",
         task_description="Verify an anonymous report that PC 9002 attended a licensed premises "
                           "in uniform outside of duty hours.",
         priority="Low", due_date="10 Oct 2026"),
    dict(source="External agency referral", subject_code=None,
         task_description="Respond to an NCA request for information supporting a joint "
                           "intelligence development.",
         priority="High", due_date="16 Sep 2026", allocate=INTEL_CELL, advance=True),
]

NIA_RECORDS = [
    dict(subject_code="PC 7734", status="Under review", opened_at="15 Sep 2026",
         next_review="15 Mar 2027",
         summary="Notifiable association arising from the CCU/7734 referral -- business "
                 "directorship links now being monitored pending the investigation outcome.",
         detail=""),
    dict(subject_code="PC 5521", status="Restricted", opened_at="02 Sep 2026",
         next_review="02 Dec 2026",
         summary="Reported association with an individual linked to an active organised crime "
                 "investigation; restricted pending OCG unit liaison.",
         detail=""),
    dict(subject_code="PC 4477", status="Managed", opened_at="11 Jan 2026",
         next_review="11 Jan 2027",
         summary="Historic association with a licensed premises, since resolved; subject to "
                 "routine annual review only.",
         detail=""),
]

BUSINESS_INTERESTS = [
    dict(subject_code="PC 7734", status="Under review", opened_at="16 Sep 2026",
         next_review="16 Dec 2026",
         summary="Directorship in a private security company, declared retrospectively "
                 "following the CCU referral; awaiting Force Solicitor review for conflict "
                 "with core duties.",
         detail=""),
    dict(subject_code="PC 2299", status="Approved", opened_at="04 Feb 2026",
         next_review="04 Feb 2027",
         summary="Secondary employment as a part-time fitness instructor, evenings and "
                 "weekends only; no conflict identified.",
         detail=""),
    dict(subject_code="PC 6654", status="Refused", opened_at="19 Mar 2026",
         next_review="19 Mar 2027",
         summary="Application for a personal alcohol licence refused due to conflict with "
                 "neighbourhood policing duties in the same ward as the premises.",
         detail=""),
]


def seed_cases(client: httpx.Client) -> None:
    for spec in CASES:
        print(f"case: {spec['subject_code']} — {spec['category']}")
        body = {k: spec[k] for k in (
            "subject_code", "source", "category", "source_evaluation", "intelligence_evaluation",
            "handling_code", "handling_conditions", "gsc", "source_reference", "crime_ref",
            "sanitised", "review_date", "summary",
        )}
        created = call(client, "POST", "/api/cases", DC_MARSH, json=body)
        case_id = created["id"]
        print(f"  -> {created['ref']}")

        adc = spec.get("adc")
        leave_unallocated = bool(adc and adc.get("leave_unallocated"))
        if not leave_unallocated:
            call(client, "POST", f"/api/cases/{case_id}/officer", SUPERVISOR,
                 json={"officer_principal": DI_GRAINGER[0] if spec.get("full_walkthrough") else DC_MARSH[0]})

        if spec.get("risk"):
            r = spec["risk"]
            call(client, "POST", f"/api/cases/{case_id}/risk", DC_MARSH,
                 json={"impact": r["impact"], "likelihood": r["likelihood"], "rationale": r["rationale"]})

        for action in spec.get("actions", []):
            call(client, "POST", f"/api/cases/{case_id}/actions", DC_MARSH, json={
                "title": action["title"],
                "owner_principal": action["owner"][0],
                "priority": action["priority"],
                "due_date": action["due_date"],
            })

        for update in spec.get("updates", []):
            call(client, "POST", f"/api/cases/{case_id}/updates", DC_MARSH, json=update)

        if adc:
            call(client, "POST", f"/api/cases/{case_id}/adc", SUPERVISOR, json={
                "decision": adc["decision"],
                "rationale": adc["rationale"],
                "action_owner_principal": DC_MARSH[0],
            })

        if spec.get("full_walkthrough"):
            report = call(client, "POST", f"/api/cases/{case_id}/report/generate", DI_GRAINGER)
            report_id = report["report_id"]
            statements = call(client, "GET", "/api/attestation-statements", DI_GRAINGER)
            call(client, "POST", f"/api/reports/{report_id}/submit", DI_GRAINGER, json={
                "typed_name": DI_GRAINGER[1],
                "statements_ticked": [True] * len(statements),
            })
            call(client, "POST", f"/api/reports/{report_id}/approve", SUPERVISOR)
            print(f"  -> report {report_id} generated, submitted and approved")


def seed_hub_taskings(client: httpx.Client) -> None:
    for spec in HUB_TASKINGS:
        print(f"hub tasking: {spec['task_description'][:60]}...")
        created = call(client, "POST", "/api/hub-taskings", INTEL_CELL, json={
            "source": spec["source"],
            "subject_code": spec["subject_code"],
            "task_description": spec["task_description"],
            "priority": spec["priority"],
            "due_date": spec["due_date"],
        })
        print(f"  -> {created['ref']}")
        if spec.get("allocate"):
            call(client, "POST", f"/api/hub-taskings/{created['id']}/officer", SUPERVISOR,
                 json={"officer_principal": spec["allocate"][0]})
        if spec.get("advance"):
            call(client, "POST", f"/api/hub-taskings/{created['id']}/stage", SUPERVISOR,
                 json={"stage": "In progress"})


def seed_nia(client: httpx.Client) -> None:
    for spec in NIA_RECORDS:
        print(f"NIA: {spec['subject_code']}")
        created = call(client, "POST", "/api/nia", INTEL_CELL, json=spec)
        print(f"  -> {created['ref']}")


def seed_business_interests(client: httpx.Client) -> None:
    for spec in BUSINESS_INTERESTS:
        print(f"business interest: {spec['subject_code']}")
        created = call(client, "POST", "/api/business-interests", INTEL_CELL, json=spec)
        print(f"  -> {created['ref']}")


def main() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=30.0, auth=SITE_AUTH) as client:
        health = client.get("/api/health").json()
        print(f"Connected to {BASE_URL} -- modes: {health['modes']}")
        if health["modes"]["environment"] == "production":
            print("Refusing to seed fictional data into a production-mode server.")
            sys.exit(1)

        seed_cases(client)
        seed_hub_taskings(client)
        seed_nia(client)
        seed_business_interests(client)

        print("\nDone. All data above is fictional -- invented for demo/training use only.")


if __name__ == "__main__":
    main()
