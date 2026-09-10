"""
Configuration resolution: real environment > .env file > safe default.

Never let callers test raw strings for "is this feature on" — expose a
`*_configured()` predicate instead, and keep every predicate here so the
health endpoint can report all of them in one place.
"""
import os
from pathlib import Path

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
_dotenv_values: dict[str, str] = {}

if _ENV_FILE.exists():
    for line in _ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        _dotenv_values[key.strip()] = value.strip().strip('"').strip("'")


def get(name: str, default: str = "") -> str:
    """Real environment wins, then .env file, then the given default."""
    if name in os.environ and os.environ[name] != "":
        return os.environ[name]
    if name in _dotenv_values and _dotenv_values[name] != "":
        return _dotenv_values[name]
    return default


def get_bool(name: str, default: bool = False) -> bool:
    val = get(name, "")
    if val == "":
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def get_list(name: str) -> list[str]:
    """Comma-separated env values -> list of stripped, non-empty strings."""
    raw = get(name, "")
    return [p.strip() for p in raw.split(",") if p.strip()]


# ---- App identity / environment ----
ENVIRONMENT = get("ENVIRONMENT", "development")
PORT = int(get("PORT", "8000"))
APP_NAME = "NOVA_CCU"

# ---- Anthropic (chat) ----
ANTHROPIC_API_KEY = get("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = get("ANTHROPIC_MODEL", "claude-sonnet-5")


def anthropic_configured() -> bool:
    return bool(ANTHROPIC_API_KEY)


def embedding_configured() -> bool:
    # No RAG/embeddings pipeline exists in Nova-CCU yet (see STATUS.md) --
    # stays false until one is actually built, rather than gating on
    # unrelated chat credentials.
    return False


# ---- Document Intelligence (OCR) ----
AZURE_DOCINTEL_ENDPOINT = get("AZURE_DOCINTEL_ENDPOINT")
AZURE_DOCINTEL_KEY = get("AZURE_DOCINTEL_KEY")
OCR_DISABLED = get_bool(f"{APP_NAME}_OCR", default=True) is False  # explicit opt-out


def ocr_configured() -> bool:
    if OCR_DISABLED:
        return False
    return bool(AZURE_DOCINTEL_ENDPOINT and AZURE_DOCINTEL_KEY)


# ---- Entra / auth ----
AZURE_AD_CLIENT_ID = get("AZURE_AD_CLIENT_ID")
AZURE_AD_CLIENT_SECRET = get("AZURE_AD_CLIENT_SECRET")
AZURE_AD_TENANT_ID = get("AZURE_AD_TENANT_ID")
SESSION_SECRET_KEY = get("SESSION_SECRET_KEY")
TRUST_EASYAUTH = get_bool(f"{APP_NAME}_TRUST_EASYAUTH", default=False)


def msal_configured() -> bool:
    return bool(AZURE_AD_CLIENT_ID and AZURE_AD_CLIENT_SECRET and AZURE_AD_TENANT_ID and SESSION_SECRET_KEY)


# ---- Shared front-door gate (optional) ----
# A coarse HTTP Basic Auth challenge in front of the whole app -- for a
# semi-public deployment (e.g. a shared Render URL) that has no real
# per-user auth yet. Off by default (blank password); dev-fallback identity
# still applies underneath it. Not a substitute for real sign-in.
SITE_USERNAME = get(f"{APP_NAME}_SITE_USERNAME", "nova-ccu")
SITE_PASSWORD = get(f"{APP_NAME}_SITE_PASSWORD")


def site_gate_configured() -> bool:
    return bool(SITE_PASSWORD)


# ---- Roles bootstrap ----
ADMINS = get_list(f"{APP_NAME}_ADMINS")
SUPERVISORS = get_list(f"{APP_NAME}_SUPERVISORS")

# ---- Data / storage ----
DATA_DIR = get(f"{APP_NAME}_DATA_DIR", str(Path(__file__).resolve().parent.parent / "data"))

# ---- SMTP ----
SMTP_HOST = get("SMTP_HOST")
SMTP_PORT = get("SMTP_PORT")
SMTP_USERNAME = get("SMTP_USERNAME")
SMTP_PASSWORD = get("SMTP_PASSWORD")
SMTP_FROM_EMAIL = get("SMTP_FROM_EMAIL")
SMTP_USE_TLS = get_bool("SMTP_USE_TLS", default=True)


def smtp_configured() -> bool:
    return bool(SMTP_HOST and SMTP_PORT and SMTP_FROM_EMAIL)


# ---- Central audit forwarding (outbox sink) ----
AUDIT_SINK_URL = get("AUDIT_SINK_URL")
AUDIT_SOURCE_ID = get("AUDIT_SOURCE_ID")
AUDIT_SOURCE_KEY = get("AUDIT_SOURCE_KEY")


def audit_forwarding_configured() -> bool:
    return bool(AUDIT_SINK_URL and AUDIT_SOURCE_ID and AUDIT_SOURCE_KEY)


def all_modes() -> dict:
    """Everything /api/health reports. Add new predicates here, not ad hoc."""
    return {
        "environment": ENVIRONMENT,
        "llm": "live" if anthropic_configured() else "mock",
        "embeddings": "azure" if embedding_configured() else "keyword-fallback",
        "ocr": "azure" if ocr_configured() else "off",
        "auth": (
            "easyauth" if TRUST_EASYAUTH else "msal" if msal_configured() else "dev-fallback"
        ),
        "smtp": "live" if smtp_configured() else "record-and-download",
        "audit_forwarding": "on" if audit_forwarding_configured() else "off",
    }
