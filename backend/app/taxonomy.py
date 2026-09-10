"""
Controlled vocabularies -- the human's words.

Every value here is something an officer or supervisor selects from a
dropdown or chip list. The model is never asked to choose one of these; it
may only be shown the choice that was made, or asked to write prose that
refers to it.
"""

STAGES = [
    "File created",
    "ADC Complete, awaiting allocation",
    "In progress",
    "With DS for review",
    "Case Finalised",
]

HUB_STAGES = ["Received", "Allocated", "In progress", "Complete"]

RISK_LEVELS = ["Low", "Medium", "High"]  # no "Critical" -- deliberately removed


def risk_band_from_score(impact: int, likelihood: int) -> str:
    score = impact * likelihood
    if score >= 13:
        return "High"
    if score >= 6:
        return "Medium"
    return "Low"


SOURCE_TYPES = [
    "Internal referral", "PSD referral", "Public report", "Anonymous",
    "Vetting referral", "External agency referral", "Hotline report",
    "Intelligence submission",
]

INTEL_CATEGORIES = [
    "Corruption \u2014 financial",
    "Corruption \u2014 relationships/associations",
    "Abuse of position",
    "Data misuse",
    "Substance misuse",
    "Notifiable association",
    "Vetting concern",
    "Other",
]

# 3x5x2 grading -- the actual College of Policing model, not 5x5x5.
SOURCE_EVALUATION = ["Reliable", "Untested", "Not reliable"]  # no letter codes
INTELLIGENCE_EVALUATION = ["A", "B", "C", "D", "E"]
HANDLING_CODES = ["P", "C"]  # P = permits sharing, C = permits with conditions
HANDLING_CONDITIONS = [
    "None",
    "A1 \u2014 Covert development",
    "A2 \u2014 Covert use",
    "A3 \u2014 Overt use",
    "S1 \u2014 Delegated authority (sanitisation permitted)",
    "S2 \u2014 Consult originator before sanitisation",
]
GSC_LEVELS = ["OFFICIAL", "OFFICIAL-SENSITIVE", "SECRET", "TOP SECRET"]
YES_NO = ["No", "Yes"]

# ADC decision -- exactly these four, nothing else.
ADC_DECISIONS = [
    "No further action",
    "Record Intel and Forward to PSD",
    "Vetting Referral",
    "CCU Investigation",
]

ACTION_PRIORITIES = ["Low", "Medium", "High"]
ACTION_STATUSES = ["Not started", "In progress", "Complete"]

# Who is entering a case update. Andrew's convention prefers this be a real
# officer identity where possible (see identity.py) -- these role labels are
# stored alongside the entry for readability, not as a substitute for it.
UPDATE_ENTRY_ROLES = ["OIC", "Supervisor", "Inspector"]

NIA_STATUSES = ["Managed", "Under review", "Restricted"]
BI_STATUSES = ["Approved", "Under review", "Refused"]

IMPACT_LABELS = [
    "1 \u2014 Negligible", "2 \u2014 Minor", "3 \u2014 Moderate",
    "4 \u2014 Major", "5 \u2014 Severe",
]
LIKELIHOOD_LABELS = [
    "1 \u2014 Rare", "2 \u2014 Unlikely", "3 \u2014 Possible",
    "4 \u2014 Likely", "5 \u2014 Almost certain",
]


def validate(value: str, options: list[str], field_name: str) -> str:
    """
    Validate any label matched against its controlled vocabulary before it
    is stored or used to branch logic. An off-list value silently hid a
    whole UI panel during Nova-PSD testing -- never trust a raw string here.
    """
    if value not in options:
        raise ValueError(f"'{value}' is not a valid {field_name}. Expected one of: {options}")
    return value
