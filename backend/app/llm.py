"""
Anthropic-or-mock chat. Called over raw httpx -- no SDK version drift.

Degrade-loud: with no API key, chat() returns a deterministic mock that
echoes the assembled context, prefixed "[OFFLINE PLACEHOLDER -- ...]". The
whole workflow must run end to end with no network at all. Never hide this
from the officer -- mode() is surfaced in the UI and on /api/health as
"live" / "mock", never a vendor name (officer-facing text stays vendor-free
per the house style).

Gotchas:
  - Set proxy=None on outbound httpx calls -- inheriting the corporate
    proxy from the shell has produced intermittent 407s on some networks.
  - An empty completion is usually truncation (stop_reason "max_tokens"),
    not a refusal -- surface stop_reason, never return blank.
"""
import httpx

from . import config

ANTHROPIC_API_VERSION = "2023-06-01"


def mode() -> str:
    return "live" if config.anthropic_configured() else "mock"


class LLMResult:
    def __init__(self, text: str, finish_reason: str, mode_used: str):
        self.text = text
        self.finish_reason = finish_reason
        self.mode_used = mode_used


def _mock_chat(prompt: str) -> LLMResult:
    snippet = prompt.strip().replace("\n", " ")
    if len(snippet) > 400:
        snippet = snippet[:400] + "..."
    text = (
        "[OFFLINE PLACEHOLDER -- no AI provider configured. "
        "This is not a drafted report; it echoes what would have been sent "
        "to the model so the workflow can be exercised end to end with no "
        "network access.]\n\n"
        f"Prompt received ({len(prompt)} chars): {snippet}"
    )
    return LLMResult(text=text, finish_reason="mock", mode_used="mock")


async def chat(prompt: str, max_output_tokens: int = 1000) -> LLMResult:
    if not config.anthropic_configured():
        return _mock_chat(prompt)

    headers = {
        "x-api-key": config.ANTHROPIC_API_KEY,
        "anthropic-version": ANTHROPIC_API_VERSION,
        "Content-Type": "application/json",
    }
    body = {
        "model": config.ANTHROPIC_MODEL,
        "max_tokens": max_output_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }

    async with httpx.AsyncClient(proxy=None, timeout=60.0) as client:
        try:
            resp = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=body)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            return LLMResult(
                text=f"[AI DRAFTING FAILED -- {e.response.status_code} from Anthropic. Draft manually.]",
                finish_reason="error",
                mode_used="live",
            )
        except httpx.RequestError as e:
            return LLMResult(
                text=f"[AI DRAFTING FAILED -- network error ({e.__class__.__name__}). Draft manually.]",
                finish_reason="error",
                mode_used="live",
            )

    data = resp.json()
    stop_reason = data.get("stop_reason", "unknown")
    text = "".join(block.get("text", "") for block in (data.get("content") or []) if block.get("type") == "text")

    if not text:
        # Usually truncation (stop_reason "max_tokens") -- surface it
        # rather than returning blank.
        text = f"[AI DRAFTING RETURNED NO TEXT -- stop_reason: {stop_reason}. Draft manually.]"

    return LLMResult(text=text, finish_reason=stop_reason, mode_used="live")
