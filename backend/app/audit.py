"""
Hash-chained audit trail.

record() appends a row whose hash covers the previous row's hash, so any
retro-edit or deletion breaks the chain from that point on. The acting user
and the linked case are folded into the *hashed* detail (not just plain
columns) so attribution itself is tamper-evident -- they're also mirrored
into indexed columns purely for filtering.

The actor is read from a contextvars.ContextVar set by HTTP middleware, so
no call site has to pass it explicitly. Background sweeps record as "system".
"""
import contextvars
import hashlib
import json
import threading
from datetime import datetime, timezone

from . import config, storage

GENESIS_HASH = "0" * 64

_write_lock = threading.Lock()

current_actor: contextvars.ContextVar[str] = contextvars.ContextVar("current_actor", default="system")
current_identity_source: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_identity_source", default="unknown"
)


def _row_hash(prev_hash: str, ts: str, event_type: str, item_id: str, detail_json: str) -> str:
    payload = f"{prev_hash}|{ts}|{event_type}|{item_id}|{detail_json}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record(event_type: str, item_id: str = "", detail: dict | None = None, case_ref: str | None = None) -> int:
    """
    Append one audit row. Thread-safe: a lock serialises read-prev-then-write
    so concurrent requests cannot fork the chain.
    """
    actor = current_actor.get()
    identity_source = current_identity_source.get()
    detail = dict(detail or {})
    detail["_actor"] = actor
    detail["_case"] = case_ref or ""

    ts = datetime.now(timezone.utc).isoformat()
    detail_json = json.dumps(detail, sort_keys=True, default=str)

    with _write_lock:
        conn = storage.get_conn()
        prev = conn.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        prev_hash = prev["hash"] if prev else GENESIS_HASH

        row_hash = _row_hash(prev_hash, ts, event_type, item_id, detail_json)

        cur = conn.execute(
            """INSERT INTO audit_log
               (ts, event_type, item_id, actor, identity_source, case_ref, detail_json, prev_hash, hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ts, event_type, item_id, actor, identity_source, case_ref, detail_json, prev_hash, row_hash),
        )
        audit_id = cur.lastrowid

        if config.audit_forwarding_configured():
            conn.execute("INSERT INTO audit_outbox (audit_id) VALUES (?)", (audit_id,))

        conn.commit()

    return audit_id


def verify_chain() -> tuple[bool, int | None]:
    """Recompute every row's hash. Returns (ok, first_broken_id_or_None)."""
    conn = storage.get_conn()
    rows = conn.execute(
        "SELECT id, ts, event_type, item_id, detail_json, prev_hash, hash FROM audit_log ORDER BY id ASC"
    ).fetchall()

    expected_prev = GENESIS_HASH
    for row in rows:
        if row["prev_hash"] != expected_prev:
            return False, row["id"]
        recomputed = _row_hash(expected_prev, row["ts"], row["event_type"], row["item_id"] or "", row["detail_json"])
        if recomputed != row["hash"]:
            return False, row["id"]
        expected_prev = row["hash"]

    return True, None


# ---------------------------------------------------------------------------
# Human-readable rendering. An audit log nobody can read is compliance
# theatre -- this is meant to be a supervisor's actual tool.
# ---------------------------------------------------------------------------

_SENTENCE_TEMPLATES = {
    "case.created": "{actor} recorded intelligence and opened {case}.",
    "case.officer_reassigned": "{actor} reassigned {case} to a new officer in charge.",
    "case.risk_updated": "{actor} updated the risk assessment on {case}.",
    "case.adc_decision": "{actor} recorded an ADC decision on {case}.",
    "case.action_added": "{actor} added an action to {case}.",
    "case.action_status_changed": "{actor} changed an action's status on {case}.",
    "case.update_added": "{actor} added a case update to {case}.",
    "report.generated": "{actor} generated a draft report for {case}.",
    "report.submitted": "{actor} submitted the report for {case} for supervisor review.",
    "report.attested": "{actor} attested to the report for {case}.",
    "report.approved": "{actor} approved the report for {case}.",
    "report.returned": "{actor} sent the report for {case} back for changes.",
    "auth.denied": "{actor} was denied access to a restricted action.",
}


def render_history(rows) -> list[dict]:
    """Turn raw audit rows into display-ready sentences with day headers."""
    out = []
    for row in rows:
        detail = json.loads(row["detail_json"])
        template = _SENTENCE_TEMPLATES.get(row["event_type"], "{actor} performed {event_type} on {case}.")
        sentence = template.format(
            actor=detail.get("_actor", row["actor"]),
            case=detail.get("_case") or row["case_ref"] or "an item",
            event_type=row["event_type"],
        )
        out.append(
            {
                "id": row["id"],
                "ts": row["ts"],
                "day": row["ts"][:10],
                "sentence": sentence,
                "actor": row["actor"],
                "case_ref": row["case_ref"],
                "event_type": row["event_type"],
            }
        )
    return out
