# Nova-CCU backend + frontend — status against Andrew Elliott's Nova-PSD standard

This replaces the direct-from-browser, no-backend, no-auth React prototype
with a FastAPI + SQLite backend and a vanilla HTML/CSS/JS frontend served
same-origin — matching Andrew's actual stack, not just his principles.

## What's built and actually tested (not just written)

Every item below was exercised with real HTTP requests against a running
server, not just reviewed by eye. See `TESTING.md` for exact reproduction
steps.

- **Schema + storage** (`storage.py`) — full relational schema: cases,
  intelligence, ADC decisions, investigations, actions, case updates
  (insert-only), reports, attestations, hub taskings, NIA/BI records,
  audit log + outbox. WAL mode, foreign keys on, no ORM.
- **Hash-chained audit trail** (`audit.py`) — **verified**: tampering with a
  row's detail directly in the database is detected by `/api/health`, and
  the exact broken row ID is reported.
- **Identity and roles** (`identity.py`) — dev-fallback header auth working
  now; MSAL session and EasyAuth header paths coded to the same interface.
  **Verified**: 403-never-401, visibility rules, role precedence.
- **Degrade-loud LLM** (`llm.py`) — mock mode confirmed working end to end
  with zero configuration.
- **Controlled vocabularies** (`taxonomy.py`) — the corrected 3x5x2 grading,
  5×5 risk matrix, and four real ADC options, with a `validate()` guard.
- **Provenance-tagged reports** (`report.py`) — system vs. ai sections,
  `content_stamp` staleness detection.
- **Attestation and sign-off** (`review.py`) — **verified**: wrong typed
  name rejected, incomplete attestation rejected, correct submission locks
  the report and auto-advances the case stage, self-approval blocked, a
  different supervisor can approve.
- **Derived dashboard + PIRM** (`dashboard.py`) — computed live, not stored.
- **Vanilla JS/HTML frontend** (`frontend/`) — no build step, no CDN,
  served same-origin by the FastAPI app via `StaticFiles`. Covers the full
  core loop: record intelligence (full 3x5x2 capture form) → case list →
  case detail (Intelligence / Risk / ADC / Investigation tabs) → add
  actions and case updates → generate report → edit → attestation modal
  → send to supervisor → approve/return.
  **Verified end to end** by replaying the exact sequence of API calls the
  JS makes, against a live server, checking each response at every step
  (see TESTING.md, "Full walkthrough").
- **The "second reader"** (`checks.py`) — deterministic checks (officer
  allocated, intelligence recorded, risk assessed, ADC decision present
  where due, no open high-priority actions, at least one case update)
  run automatically before submission and are shown to the officer in the
  attestation modal. They never block submission — matching Andrew's rule
  exactly — but a snapshot of the results at the moment of submission is
  stored with the attestation record, so a supervisor reviewing later can
  see exactly what was and wasn't in order when it was sent. An AI check
  (does the conclusion follow from the evidence?) is wired in but only
  runs when the LLM is live, since running it against mock placeholder
  text would just be manufactured noise.
- **Hub Taskings, NIA, Business Interests, PIRM, Master Subject** — all
  built and wired into the frontend. **Verified**: a Hub Tasking cannot
  be moved past "Received" without an officer allocated; allocation is
  supervisor-gated and auto-advances the stage; PIRM correctly aggregates
  a real test subject's score (50) across one case (+30), one NIA (+10),
  one Business Interest (+5), and one Hub Tasking (+5); the Master
  Subject view surfaces all of it in one place.
- Two more real bugs found and fixed while building this section: (1) the
  "second reader" work surfaced no new bugs itself, but building it
  exposed that (2) `resolve_identity()` was falling back to the raw
  principal string as the display name whenever a request omitted
  `X-User-Name` — meaning a user's "identity of record" silently changed
  request-to-request depending on which headers happened to be sent. This
  broke the attestation's typed-name check intermittently. Fixed to
  always prefer the name already stored against that user.
- **A serious data-visibility bug, found and fixed by actually taking
  screenshots of the running app rather than just reading the code.**
  `async def` routes (report generation, the second-reader checks) were
  intermittently returning "case not found" for cases that definitely
  existed and were visible to every other route. Root cause: FastAPI runs
  middleware and `async def` routes on the event-loop thread, while plain
  `def` routes run in a worker threadpool — two different pools of OS
  threads, each getting its own cached SQLite connection under the
  original one-connection-per-thread design. A connection reused across
  that split could return stale or empty results on reads even with
  `isolation_level=None` and no open transaction confirmed on it; a fresh
  connection opened at the exact same instant, in the exact same process,
  always saw the correct data. The precise SQLite/driver-level mechanism
  was never fully isolated despite substantial diagnosis (documented in
  `storage.py`'s `get_conn()` docstring for whoever revisits this). Rather
  than ship a caching scheme with a bug I couldn't fully explain,
  `get_conn()` now opens a fresh connection on every call instead of
  caching one per thread. This trades a small amount of per-call
  overhead — irrelevant at this application's scale — for the guarantee
  that every read reflects what was actually committed. Verified fixed
  by re-running every earlier test (attestation flow, tamper detection,
  role gating, PIRM) plus the specific async-route sequence that
  originally failed, all passing, from a clean extraction.
- Two smaller bugs fixed in the same pass: `create_case`'s two inserts
  (case + intelligence) were not wrapped in a transaction, so a failure
  partway through could leave a case with no intelligence record; and
  `taxonomy.validate()` raising a bare `ValueError` on bad input was
  surfacing as an unhandled 500 instead of a clean 422 — both fixed.

## What's not done

- **No document upload / RAG / OCR** — no clear Nova-CCU equivalent yet;
  worth deciding deliberately rather than porting reflexively.
- **No messaging/threads model.** Supervisor "return" is a single message
  field, not Andrew's full per-case-plus-direct threads model.
- **Entra auth is coded but unverified** — needs real credentials only
  your IT team can provision. Until then the app correctly runs in
  dev-fallback mode and says so on `/api/health` and in the frontend's
  mode badge.
- **AI drafting now runs live against Anthropic** (`llm.py`, swapped from
  the originally-planned Azure OpenAI once real credentials were to hand)
  when `ANTHROPIC_API_KEY` is set — see `README.md`. Falls back to mock
  mode with zero configuration, as before.
- **No hosting/deployment setup.**
- Two earlier bugs (a session-handling crash with MSAL unconfigured, and
  a role-seeding precedence bug) were found and fixed in the first pass
  of this build — see `TESTING.md` for both.

## Running it

```
cd backend
python -m venv venv
venv/bin/pip install -r ../requirements.txt
venv/bin/python -m uvicorn app.main:app --reload
```

Open `http://localhost:8000/` — the frontend loads, prompts for a
dev-fallback identity (stands in for Entra sign-in), and talks to the API
on the same origin. Try `dc.marsh@example.police.uk` (officer) or
`ds.hooker@example.police.uk` (supervisor — seeded with that role directly
in `main.py`'s `_KNOWN_OFFICERS`, independent of `NOVA_CCU_SUPERVISORS`) to
see the role-gating in the UI — e.g. the ADC tab will reject a decision
from an ordinary officer. See `README.md` for passwords now attached to
these seeded accounts.


## Reproducing the tests described above

See `TESTING.md` for the exact curl sequences used to verify each claim in
this document — every one of them was actually run against a live server
during development, not assumed.
