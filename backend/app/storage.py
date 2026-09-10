"""
All SQL lives here. Nothing else opens a connection except audit.py, which
uses this module's connector. No ORM — plain sqlite3, WAL mode, one
executescript for the schema.
"""
import sqlite3
from pathlib import Path
from contextlib import contextmanager

from . import config

Path(config.DATA_DIR).mkdir(parents=True, exist_ok=True)
DB_PATH = Path(config.DATA_DIR) / "nova_ccu.db"

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    principal TEXT UNIQUE NOT NULL,        -- email or dev-fallback identifier
    display_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'officer',  -- officer | supervisor | admin
    identity_source TEXT NOT NULL,         -- easyauth | msal | dev-fallback
    password_hash TEXT,                    -- dev-fallback demo accounts only; see identity.py
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,             -- e.g. "PC 5521"
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref TEXT UNIQUE NOT NULL,              -- "CCU/142/26"
    subject_id INTEGER NOT NULL REFERENCES subjects(id),
    stage_index INTEGER NOT NULL DEFAULT 0,
    closed INTEGER NOT NULL DEFAULT 0,
    officer_id INTEGER REFERENCES users(id),      -- NULL = OIC TBC
    source TEXT NOT NULL,
    category TEXT NOT NULL,
    risk_impact INTEGER NOT NULL DEFAULT 1,
    risk_likelihood INTEGER NOT NULL DEFAULT 1,
    risk_rationale TEXT NOT NULL DEFAULT '',
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cases_subject ON cases(subject_id);
CREATE INDEX IF NOT EXISTS idx_cases_officer ON cases(officer_id);

CREATE TABLE IF NOT EXISTS intelligence (
    case_id INTEGER PRIMARY KEY REFERENCES cases(id),
    urn TEXT UNIQUE NOT NULL,              -- "INT/2026/0871"
    received_at TEXT NOT NULL,
    source_type TEXT NOT NULL,
    gsc TEXT NOT NULL DEFAULT 'OFFICIAL-SENSITIVE',
    source_reference TEXT NOT NULL DEFAULT '',   -- ISR, never a name
    source_evaluation TEXT NOT NULL,       -- Reliable | Untested | Not reliable
    intelligence_evaluation TEXT NOT NULL, -- A-E
    handling_code TEXT NOT NULL,           -- P | C
    handling_conditions TEXT NOT NULL DEFAULT 'None',
    risk_to_source TEXT NOT NULL DEFAULT 'No',
    crime_ref TEXT NOT NULL DEFAULT '',
    sanitised TEXT NOT NULL DEFAULT '',
    review_date TEXT NOT NULL,
    summary TEXT NOT NULL,
    submitting_officer_id INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS intelligence_links (
    intelligence_case_id INTEGER NOT NULL REFERENCES intelligence(case_id),
    linked_urn TEXT NOT NULL,
    PRIMARY KEY (intelligence_case_id, linked_urn)
);

CREATE TABLE IF NOT EXISTS adc_decisions (
    case_id INTEGER PRIMARY KEY REFERENCES cases(id),
    considered_at TEXT NOT NULL,
    history_summary TEXT NOT NULL DEFAULT '',
    decision TEXT NOT NULL,     -- controlled vocabulary, see taxonomy.py
    rationale TEXT NOT NULL DEFAULT '',
    action_owner_id INTEGER REFERENCES users(id),
    decided_by INTEGER REFERENCES users(id),
    decided_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS investigations (
    case_id INTEGER PRIMARY KEY REFERENCES cases(id),
    plan TEXT NOT NULL DEFAULT '',
    review_date TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS investigation_decision_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES investigations(case_id),
    logged_at TEXT NOT NULL DEFAULT (datetime('now')),
    entry TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    title TEXT NOT NULL,
    owner_id INTEGER REFERENCES users(id),
    priority TEXT NOT NULL DEFAULT 'Medium',
    due_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Not started',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_actions_case ON actions(case_id);

-- Officer updates: INSERT-ONLY. No UPDATE or DELETE route may ever target
-- this table -- that is the whole point of it. See identity.py / main.py.
CREATE TABLE IF NOT EXISTS case_updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    entered_by INTEGER NOT NULL REFERENCES users(id),
    entered_by_role_label TEXT NOT NULL,   -- OIC / Supervisor / Inspector at time of entry
    text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_updates_case ON case_updates(case_id);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    content_stamp TEXT NOT NULL,     -- hash of the inputs this was built from
    body TEXT NOT NULL,              -- final text, officer-edited
    ai_sections_json TEXT NOT NULL,  -- {section_name: raw_ai_text} for provenance display
    status TEXT NOT NULL DEFAULT 'draft',  -- draft | submitted | returned | approved
    created_by INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The five attestation statements, stored verbatim with the submission so
-- what was signed can never be in doubt. See review.py.
CREATE TABLE IF NOT EXISTS attestations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES reports(id),
    statements_json TEXT NOT NULL,   -- the exact statements shown, all ticked
    typed_name TEXT NOT NULL,
    signed_by INTEGER NOT NULL REFERENCES users(id),
    signed_at TEXT NOT NULL DEFAULT (datetime('now')),
    content_stamp_at_signing TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hub_taskings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref TEXT UNIQUE NOT NULL,          -- "Hub/187/26"
    source TEXT NOT NULL,
    subject_id INTEGER REFERENCES subjects(id),   -- NULL = not subject-specific
    task_description TEXT NOT NULL,
    allocated_officer_id INTEGER REFERENCES users(id),  -- NULL = OIC TBC
    priority TEXT NOT NULL DEFAULT 'Medium',
    due_date TEXT NOT NULL,
    stage_index INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS nia_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref TEXT UNIQUE NOT NULL,          -- "NIA/126/26"
    subject_id INTEGER NOT NULL REFERENCES subjects(id),
    status TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    next_review TEXT NOT NULL,
    summary TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS business_interests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref TEXT UNIQUE NOT NULL,          -- "BI/094/26"
    subject_id INTEGER NOT NULL REFERENCES subjects(id),
    status TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    next_review TEXT NOT NULL,
    summary TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);

-- Hash-chained audit log. Every row's hash covers the previous row's hash,
-- so any retro-edit or deletion breaks the chain from that point on.
-- Actor and case are folded into the hashed detail (not just plain columns)
-- so attribution itself is tamper-evident. See audit.py.
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL DEFAULT (datetime('now')),
    event_type TEXT NOT NULL,
    item_id TEXT,
    actor TEXT NOT NULL,            -- principal, or "system"
    identity_source TEXT NOT NULL DEFAULT 'unknown',
    case_ref TEXT,
    detail_json TEXT NOT NULL,      -- includes _actor and _case, hashed
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_case ON audit_log(case_ref);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor);

CREATE TABLE IF NOT EXISTS audit_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id INTEGER NOT NULL REFERENCES audit_log(id),
    sent INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt_at TEXT
);
"""


def get_conn() -> sqlite3.Connection:
    """
    A fresh connection every call, deliberately not cached.

    This used to be one cached connection per thread (via threading.local),
    which is the standard pattern -- but it broke in a way worth recording.
    An async route handler's connection was demonstrably returning zero
    rows from a table that a fresh connection, opened at the exact same
    instant in the exact same process, correctly saw fully populated.
    isolation_level=None (true autocommit) and confirming in_transaction
    was False on the stale connection ruled out the obvious "pinned read
    transaction" explanation. The precise mechanism was never fully
    isolated -- it did not reproduce with a fresh connection under any
    condition tested, only with a connection reused across FastAPI's mix
    of event-loop-thread (async routes, middleware) and threadpool-thread
    (sync routes) execution.

    Rather than ship a caching scheme with a bug I couldn't fully explain,
    this trades a small amount of per-call connection overhead (irrelevant
    at this application's scale) for the guarantee that every read reflects
    reality. If this needs to be revisited for performance at higher scale,
    do so with a load test that specifically exercises the async+sync route
    mix, not just a raw throughput benchmark.
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db() -> None:
    conn = get_conn()
    conn.executescript(SCHEMA)
    # Guarded ALTER for a schema change on an already-existing db file --
    # new installs get the column straight from SCHEMA above. Never a
    # migration framework for a single-file SQLite app, per the build notes.
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "password_hash" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
    conn.commit()


@contextmanager
def tx():
    """
    Explicit transaction boundary for multi-statement writes. Needed because
    get_conn() now uses isolation_level=None (autocommit) to avoid pinning
    stale read snapshots -- so without this, each statement in a supposedly
    atomic multi-statement write would commit individually, and a failure
    partway through would leave the database in a half-written state.
    """
    conn = get_conn()
    conn.execute("BEGIN")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


# ---------------------------------------------------------------------------
# Subjects
# ---------------------------------------------------------------------------

def get_or_create_subject(code: str) -> int:
    conn = get_conn()
    row = conn.execute("SELECT id FROM subjects WHERE code = ?", (code,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO subjects (code) VALUES (?)", (code,))
    conn.commit()
    return cur.lastrowid


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def get_user_by_principal(principal: str) -> sqlite3.Row | None:
    return get_conn().execute("SELECT * FROM users WHERE principal = ?", (principal,)).fetchone()


def create_user(principal: str, display_name: str, role: str, identity_source: str) -> sqlite3.Row:
    conn = get_conn()
    conn.execute(
        "INSERT INTO users (principal, display_name, role, identity_source) VALUES (?, ?, ?, ?)",
        (principal, display_name, role, identity_source),
    )
    conn.commit()
    return get_user_by_principal(principal)


def set_user_role(principal: str, role: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE users SET role = ? WHERE principal = ?", (role, principal))
    conn.commit()


def set_user_password(principal: str, password_hash: str) -> None:
    conn = get_conn()
    conn.execute("UPDATE users SET password_hash = ? WHERE principal = ?", (password_hash, principal))
    conn.commit()
