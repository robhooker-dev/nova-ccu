"""
Who is this, what may they see, what may they do.

Three acquisition paths, strongest first:
  1. Platform EasyAuth headers -- spoof-proof only because the platform
     injects them. Honoured ONLY when NOVA_CCU_TRUST_EASYAUTH is set.
  2. In-app MSAL session -- verified user in a signed session cookie.
  3. Dev fallback -- asserted X-User-* headers, local only.

The audit row always records identity_source, so the trail is honest about
how strong the identity actually was for that action.

Roles: officer < supervisor < admin, bootstrapped from an env allow-list and
managed thereafter in the users table. Verified identities are
auto-provisioned as officers on first sight.

Precedence gotcha (kept from Nova-PSD, applies here too): env admin list >
users-table role > env supervisor list. Once a user row exists, adding the
principal to the supervisor env list will NOT promote them -- change the
role in the UI/DB.
"""
import hashlib
import hmac
import secrets
from dataclasses import dataclass

from fastapi import Request, HTTPException

from . import config, storage

ROLE_RANK = {"officer": 0, "supervisor": 1, "admin": 2}


def hash_password(password: str) -> str:
    """
    PBKDF2 via stdlib -- adequate for the dev-fallback demo accounts this
    gates (see main.py's dev-login route), not a claim of production-grade
    auth. Stored as "salt$digest", both hex.
    """
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 100_000).hex()
    return f"{salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 100_000).hex()
    return hmac.compare_digest(candidate, digest)


@dataclass
class Identity:
    principal: str
    display_name: str
    role: str
    identity_source: str  # "easyauth" | "msal" | "dev-fallback"

    def has_role(self, minimum: str) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[minimum]


def _resolve_from_easyauth(request: Request) -> tuple[str, str] | None:
    if not config.TRUST_EASYAUTH:
        return None
    principal = request.headers.get("X-MS-CLIENT-PRINCIPAL-NAME")
    name = request.headers.get("X-MS-CLIENT-PRINCIPAL-ID", principal)
    if not principal:
        return None
    return principal, name or principal


def _resolve_from_msal_session(request: Request) -> tuple[str, str] | None:
    try:
        session_user = request.session.get("user")
    except AssertionError:
        return None  # SessionMiddleware only added when MSAL is configured
    if not session_user:
        return None
    return session_user["principal"], session_user.get("display_name", session_user["principal"])


def _resolve_from_dev_fallback(request: Request) -> tuple[str, str] | None:
    if config.ENVIRONMENT == "production":
        return None  # dev fallback never honoured in production, regardless of headers
    principal = request.headers.get("X-User-Principal")
    name = request.headers.get("X-User-Name", principal)
    if not principal:
        return None
    return principal, name


def resolve_identity(request: Request) -> Identity | None:
    for source_name, resolver in (
        ("easyauth", _resolve_from_easyauth),
        ("msal", _resolve_from_msal_session),
        ("dev-fallback", _resolve_from_dev_fallback),
    ):
        result = resolver(request)
        if result:
            principal, header_display_name = result
            role = _resolve_role(principal)
            user = storage.get_user_by_principal(principal)
            if not user:
                storage.create_user(principal, header_display_name, role, source_name)
                display_name = header_display_name
            else:
                # The stored name is the identity of record -- a request that
                # omits X-User-Name (or an EasyAuth header without a friendly
                # name) must not silently rename the user to their principal.
                display_name = user["display_name"]
            return Identity(principal=principal, display_name=display_name, role=role, identity_source=source_name)
    return None


def _resolve_role(principal: str) -> str:
    """Precedence: env admin list > users-table role > env supervisor list > officer."""
    if principal in config.ADMINS:
        return "admin"
    user = storage.get_user_by_principal(principal)
    if user:
        return user["role"]
    if principal in config.SUPERVISORS:
        return "supervisor"
    return "officer"


def require_identity(request: Request) -> Identity:
    identity = resolve_identity(request)
    if identity is None:
        # 403, never 401 -- no sign-in oracle.
        raise HTTPException(status_code=403, detail="Not signed in.")
    return identity


def require_role(request: Request, minimum: str) -> Identity:
    identity = require_identity(request)
    if not identity.has_role(minimum):
        from . import audit

        audit.current_actor.set(identity.principal)
        audit.current_identity_source.set(identity.identity_source)
        audit.record("auth.denied", detail={"required_role": minimum, "actual_role": identity.role})
        raise HTTPException(status_code=403, detail="Insufficient role for this action.")
    return identity


def visible_case_ids_clause(identity: Identity) -> tuple[str, tuple]:
    """
    SQL fragment + params for restricting a cases query to what this
    identity may see. Officers: cases they created or hold. Supervisor+:
    everything. This is deliberately a WHERE-clause helper, not a decorator,
    so every route that lists cases is forced to apply it explicitly.
    """
    if identity.has_role("supervisor"):
        return "1=1", ()
    user = storage.get_user_by_principal(identity.principal)
    user_id = user["id"] if user else -1
    return "(cases.officer_id = ? OR cases.created_by = ?)", (user_id, user_id)
