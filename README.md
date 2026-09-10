# Nova-CCU

A Counter Corruption Unit case management tool — FastAPI + SQLite backend,
vanilla HTML/CSS/JS frontend served same-origin. See `STATUS.md` for what's
built and verified, and `TESTING.md` for the exact curl sequences used to
verify it.

## Running it

```
cd backend
python -m venv venv
venv/bin/pip install -r ../requirements.txt
venv/bin/python -m uvicorn app.main:app --reload
```

Open `http://localhost:8000/`.

## Demo sign-ins

The app has no real authentication configured (no Entra/MSAL) and currently
runs in **dev-fallback** mode — you sign in by typing an email and display
name, and a user record is created automatically on first use. Any email
works this way.

Four accounts are additionally seeded with a password, so you don't have to
remember who's who or re-promote a role by hand:

| Username | Password | Role |
|---|---|---|
| `ds.hooker@example.police.uk` | `Hooker#4471` | **Supervisor** |
| `dc.marsh@example.police.uk` | `Marsh#2290` | General officer |
| `di.grainger@example.police.uk` | `Grainger#8813` | General officer |
| `intelligence.cell@example.police.uk` | `IntelCell#5541` | General officer |

Sign in as the supervisor to see the role-gated parts of the UI (ADC
decisions, officer allocation, report approval, and the "Review queue" on
the dashboard); sign in as any general officer to see it rejected.

> ⚠️ **This is a local development convenience, not real access control.**
> Passwords are checked against a PBKDF2 hash server-side (see
> `backend/app/identity.py`), which is better than nothing, but no session
> or token is issued after signing in — every API request still trusts
> whatever `X-User-Principal` header it carries, exactly as it did before
> this existed (see `backend/app/identity.py`'s dev-fallback path). Typing
> any other email straight into "Switch user" still works with no password
> at all, unchanged. Do not reuse these passwords anywhere real. A
> production deployment needs real Entra/MSAL sign-in (already coded, see
> `STATUS.md`), not this.
