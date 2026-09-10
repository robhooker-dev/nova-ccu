# Reproducing the verification tests

Run from `backend/`, with the venv already set up (`../venv/bin/python`).
Each block is self-contained — delete `data/nova_ccu.db*` between runs if
you want a clean slate.

## 1. Health / mock mode with zero configuration

```bash
rm -f data/nova_ccu.db*
../venv/bin/python -m uvicorn app.main:app --port 8010 &
sleep 2
curl -s http://localhost:8010/api/health
# Expect: "llm":"mock", "auth":"dev-fallback", "audit_chain_ok":true
```

## 2. Case creation defaults to OIC TBC, dev-fallback identity works

```bash
curl -s -X POST http://localhost:8010/api/cases -H "Content-Type: application/json" \
  -H "X-User-Principal: dc.marsh@example.police.uk" -H "X-User-Name: DC J. Marsh" \
  -d '{"subject_code":"PC 9001","source":"Internal referral","category":"Abuse of position"}'
# Expect: {"id":1,"ref":"CCU/1/26","officer":"OIC TBC"}
```

## 3. Role gating — 403, never 401

```bash
# An ordinary officer cannot record an ADC decision:
curl -s -w " [%{http_code}]" -X POST http://localhost:8010/api/cases/1/adc \
  -H "Content-Type: application/json" \
  -H "X-User-Principal: dc.marsh@example.police.uk" \
  -d '{"decision":"CCU Investigation","rationale":"x","action_owner_principal":"dc.marsh@example.police.uk"}'
# Expect: {"detail":"Insufficient role for this action."} [403]
```

## 4. Attestation-gated submission

```bash
export NOVA_CCU_SUPERVISORS="ds.hooker@example.police.uk"
# (restart the server so the env var is picked up)

REPORT=$(curl -s -X POST http://localhost:8010/api/cases/1/report/generate \
  -H "X-User-Principal: dc.marsh@example.police.uk")
REPORT_ID=$(printf '%s' "$REPORT" | python3 -c "import sys,json;print(json.load(sys.stdin)['report_id'])")

# Wrong typed name -> 422
curl -s -w " [%{http_code}]" -X POST http://localhost:8010/api/reports/$REPORT_ID/submit \
  -H "Content-Type: application/json" -H "X-User-Principal: dc.marsh@example.police.uk" \
  -d '{"typed_name":"Someone Else","statements_ticked":[true,true,true,true,true]}'

# Not all statements ticked -> 422
curl -s -w " [%{http_code}]" -X POST http://localhost:8010/api/reports/$REPORT_ID/submit \
  -H "Content-Type: application/json" -H "X-User-Principal: dc.marsh@example.police.uk" \
  -d '{"typed_name":"DC J. Marsh","statements_ticked":[true,true,true,false,true]}'

# Correct -> 200, and case stage auto-advances
curl -s -X POST http://localhost:8010/api/reports/$REPORT_ID/submit \
  -H "Content-Type: application/json" -H "X-User-Principal: dc.marsh@example.police.uk" \
  -d '{"typed_name":"DC J. Marsh","statements_ticked":[true,true,true,true,true]}'
```

**Important**: `X-User-Name` must be supplied on *every* request for a
given principal for the typed-name match to work sensibly in this dev
harness — the display name comes from whatever was sent the first time
that principal was seen (it isn't re-read from headers if the user row
already exists).

## 5. Nobody approves their own submission

```bash
# Marsh (submitter) tries to approve -> blocked by role gate (he's not a supervisor)
curl -s -w " [%{http_code}]" -X POST http://localhost:8010/api/reports/$REPORT_ID/approve \
  -H "X-User-Principal: dc.marsh@example.police.uk"
# Expect 403

# Hooker (supervisor, did not submit) -> 200
curl -s -w " [%{http_code}]" -X POST http://localhost:8010/api/reports/$REPORT_ID/approve \
  -H "X-User-Principal: ds.hooker@example.police.uk"
```

## 6. The audit chain actually detects tampering

```bash
# Stop the server first.
../venv/bin/python -c "
import sqlite3
conn = sqlite3.connect('data/nova_ccu.db')
conn.execute('UPDATE audit_log SET detail_json = ? WHERE id = 1', ('{\"tampered\": true}',))
conn.commit()
"
# Restart the server, then:
curl -s http://localhost:8010/api/health
# Expect: "audit_chain_ok":false, "audit_chain_broken_at":1
```

## 7. Full walkthrough (what the frontend actually does, replayed via curl)

```bash
export NOVA_CCU_SUPERVISORS="ds.hooker@example.police.uk"
# restart the server after setting this

BASE=http://localhost:8010

# 1. Record intelligence -> creates case + full 3x5x2 intelligence record
CASE=$(curl -s -X POST $BASE/api/cases -H "Content-Type: application/json" \
  -H "X-User-Principal: dc.marsh@example.police.uk" -H "X-User-Name: DC J. Marsh" \
  -d '{"subject_code":"PC 9010","source":"Vetting referral","category":"Corruption \u2014 financial","source_evaluation":"Reliable","intelligence_evaluation":"B","handling_code":"P","handling_conditions":"None","gsc":"OFFICIAL-SENSITIVE","source_reference":"","crime_ref":"","sanitised":"","review_date":"07 September 2027","summary":"Undeclared business interest identified."}')
CASE_ID=$(printf '%s' "$CASE" | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")

# 2. Fetch combined case detail (case + intel + adc + investigation + actions + updates + reports)
curl -s $BASE/api/cases/$CASE_ID -H "X-User-Principal: dc.marsh@example.police.uk"

# 3. Supervisor allocates the officer
curl -s -X POST $BASE/api/cases/$CASE_ID/officer -H "Content-Type: application/json" \
  -H "X-User-Principal: ds.hooker@example.police.uk" \
  -d '{"officer_principal":"dc.marsh@example.police.uk"}'

# 4. Risk assessment
curl -s -X POST $BASE/api/cases/$CASE_ID/risk -H "Content-Type: application/json" \
  -H "X-User-Principal: dc.marsh@example.police.uk" \
  -d '{"impact":4,"likelihood":4,"rationale":"High impact and likelihood based on findings."}'

# 5. Add an action
curl -s -X POST $BASE/api/cases/$CASE_ID/actions -H "Content-Type: application/json" \
  -H "X-User-Principal: dc.marsh@example.police.uk" \
  -d '{"title":"Obtain Companies House records","owner_principal":"dc.marsh@example.police.uk","priority":"High","due_date":"15 Sep 2026"}'

# 6. Add a case update (the OIC's recommendation lives here)
curl -s -X POST $BASE/api/cases/$CASE_ID/updates -H "Content-Type: application/json" \
  -H "X-User-Principal: dc.marsh@example.police.uk" \
  -d '{"text":"Recommend referral to PSD given the financial disclosure findings.","role_label":"OIC"}'

# 7. Generate the report
curl -s -X POST $BASE/api/cases/$CASE_ID/report/generate -H "X-User-Principal: dc.marsh@example.police.uk"
```

All of the above was run and its output checked at every step during
development — not assumed to work because the code looked right.

## Known bugs found and fixed during this build (not left latent)

1. `request.session` raised `AssertionError` (not caught by `hasattr`) when
   `SessionMiddleware` wasn't installed, crashing every request in
   dev-fallback mode. Fixed in `identity.py`.
2. `_seed_known_officers()` originally hardcoded `role="officer"` when
   creating the four known officer rows, which — per the documented
   precedence rule (env admin list > users-table role > env supervisor
   list) — would have permanently locked them out of ever being promoted
   by `NOVA_CCU_SUPERVISORS`, since a user row would already exist by the
   time that env var was consulted. Fixed to resolve the role correctly
   at seed time in `main.py`.

## Known test-harness gotchas (not application bugs)

- **`/bin/sh`'s builtin `echo` interprets backslash escapes by default.**
  Piping a JSON string containing `\n` through `echo "$VAR" | python3 -c ...`
  will corrupt it into a real newline and break JSON parsing. Use
  `printf '%s' "$VAR"` instead.
- **Don't write to the SQLite file from a second process while the server
  is running** — a direct `sqlite3` connection from a debug script racing
  the server's own connection under WAL mode can transiently lock. Stop
  the server first, or use the app's own routes/env bootstrap instead of
  hand-editing the database (the `NOVA_CCU_SUPERVISORS` env var exists
  precisely so you don't have to).
- **Background server processes don't survive between separate shell
  invocations in some sandboxed environments.** If testing in a similar
  setup, start the server and run your curl commands in the *same* shell
  session/script rather than assuming a `&`-backgrounded process persists.
