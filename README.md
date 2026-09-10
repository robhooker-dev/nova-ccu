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

## Deploying (Render)

`render.yaml` at the repo root defines the service as a Blueprint: Python
web service, a 1GB persistent disk mounted at `/var/data` for the SQLite
file (the free plan's storage is ephemeral and would wipe the database on
every restart, so this needs at least the Starter plan), and a health
check on `/api/health`.

1. On [dashboard.render.com](https://dashboard.render.com), **New +** →
   **Blueprint**, connect this GitHub repo. Render reads `render.yaml` and
   provisions the service.
2. Set the env vars marked `sync: false` in `render.yaml` under the
   service's **Environment** tab: `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`
   (e.g. `claude-sonnet-5`), `NOVA_CCU_SUPERVISORS`, and
   `NOVA_CCU_SITE_USERNAME` / `NOVA_CCU_SITE_PASSWORD` (see below).
3. Deploy. Render gives you a `https://nova-ccu-<random>.onrender.com` URL
   (or attach a custom domain under **Settings**).

### The site password gate

This deployment has no real per-user authentication (see "Demo sign-ins"
above) — only dev-fallback, which trusts any email typed into "Switch
user". That's fine on `localhost`; it is **not** fine on a public URL.

`NOVA_CCU_SITE_USERNAME` / `NOVA_CCU_SITE_PASSWORD`, once set, put an HTTP
Basic Auth challenge in front of the *entire* app (every route except
`/api/health`, so the platform's own health check still passes) — the
browser's native sign-in prompt, which it remembers for the session. It's
a coarse "are you even meant to be here" front door for sharing the URL
with a few colleagues, not a real access-control layer — dev-fallback
identity still applies underneath it exactly as before. Leave both blank
and the app is reachable by anyone with the link.

Before treating a Nova-CCU deployment as more than a demo, see
`STATUS.md`'s "What's not done" and wire up real Entra/MSAL sign-in
instead of relying on this gate.
