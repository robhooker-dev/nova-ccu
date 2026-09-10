"""
Azure-or-mock chat. Called over raw httpx -- no SDK version drift.

Degrade-loud: with no LLM key, chat() returns a deterministic mock that
echoes the assembled context, prefixed "[OFFLINE PLACEHOLDER -- ...]". The
whole workflow must run end to end with no network at all. Never hide this
from the officer -- mode() is surfaced in the UI and on /api/health.

Gotchas baked in here (all found the hard way, per the build notes):
  - gpt-5.x deployments reject `max_tokens` (use `max_completion_tokens`)
    and reject any non-default `temperature`. Both fail on the first call.
  - Set proxy=None on outbound httpx calls -- inheriting the corporate
    proxy from the shell produces intermittent 407s on some sections.
  - An empty completion is usually refusal or token overflow, not the
    content filter -- surface finish_reason, never return blank.
  - Investigation material can trip the content filter (it describes
    corruption, abuse, financial wrongdoing). A content-filter-relaxed
    deployment should be available for this class of tool.
"""
import httpx

from . import config


def mode() -> str:
    return "azure" if config.azure_configured() else "mock"


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
        "[OFFLINE PLACEHOLDER -- no Azure OpenAI deployment configured. "
        "This is not a drafted report; it echoes what would have been sent "
        "to the model so the workflow can be exercised end to end with no "
        "network access.]\n\n"
        f"Prompt received ({len(prompt)} chars): {snippet}"
    )
    return LLMResult(text=text, finish_reason="mock", mode_used="mock")


async def chat(prompt: str, max_output_tokens: int = 1000) -> LLMResult:
    if not config.azure_configured():
        return _mock_chat(prompt)

    url = (
        f"{config.AZURE_OPENAI_ENDPOINT}/openai/deployments/"
        f"{config.AZURE_OPENAI_DEPLOYMENT}/chat/completions"
        f"?api-version={config.AZURE_OPENAI_API_VERSION}"
    )
    headers = {"api-key": config.AZURE_OPENAI_KEY, "Content-Type": "application/json"}
    # gpt-5.x rejects `max_tokens` and non-default `temperature` -- use
    # `max_completion_tokens` and omit temperature entirely.
    body = {
        "messages": [{"role": "user", "content": prompt}],
        "max_completion_tokens": max_output_tokens,
    }

    async with httpx.AsyncClient(proxy=None, timeout=60.0) as client:
        try:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            return LLMResult(
                text=f"[AI DRAFTING FAILED -- {e.response.status_code} from Azure OpenAI. Draft manually.]",
                finish_reason="error",
                mode_used="azure",
            )
        except httpx.RequestError as e:
            return LLMResult(
                text=f"[AI DRAFTING FAILED -- network error ({e.__class__.__name__}). Draft manually.]",
                finish_reason="error",
                mode_used="azure",
            )

    data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    finish_reason = choice.get("finish_reason", "unknown")
    text = (choice.get("message") or {}).get("content", "")

    if not text:
        # An empty completion is usually refusal or token overflow, not the
        # content filter -- surface finish_reason rather than returning blank.
        text = f"[AI DRAFTING RETURNED NO TEXT -- finish_reason: {finish_reason}. Draft manually.]"

    return LLMResult(text=text, finish_reason=finish_reason, mode_used="azure")
