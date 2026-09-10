"""
Draft an intelligence submission from an uploaded document -- an email,
Word document or text-layer PDF/report. The AI only ever produces a *draft*:
the officer sees every field pre-filled in the ordinary Record Intelligence
form and must review, correct and press Create themselves. Nothing here
writes to the database.

Scope, deliberately: text-layer documents only (.txt, .eml, .docx, .pdf with
a real text layer). Scanned/image-only documents need OCR, which Nova-CCU
does not yet have (see STATUS.md) -- such a PDF is rejected with a clear
message rather than silently producing an empty draft.

No file is stored anywhere. Text is extracted in memory, sent to the model
(or not, in mock mode) purely to build the draft, and discarded once the
response is returned.
"""
import io
import json
import re

from . import llm, taxonomy

MAX_CHARS = 12000  # keep the prompt small and the cost bounded, per llm.py's own limits

SUPPORTED_EXTENSIONS = ("txt", "eml", "docx", "pdf")


def extract_text(filename: str, content: bytes) -> str:
    """Raises ValueError (-> 422 via main.py's global handler) on anything
    unreadable, so a failed extraction is always loud, never a blank draft."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext == "txt":
        return content.decode("utf-8", errors="replace")

    if ext == "eml":
        import email
        from email.policy import default as email_policy

        msg = email.message_from_bytes(content, policy=email_policy)
        parts = [f"{h}: {msg[h]}" for h in ("From", "To", "Subject", "Date") if msg[h]]
        body = msg.get_body(preferencelist=("plain", "html"))
        if body is not None:
            text = body.get_content()
            if body.get_content_type() == "text/html":
                text = re.sub(r"<[^>]+>", " ", text)
            parts.append("")
            parts.append(text)
        return "\n".join(parts)

    if ext == "docx":
        import docx

        d = docx.Document(io.BytesIO(content))
        return "\n".join(p.text for p in d.paragraphs if p.text.strip())

    if ext == "pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
        if not text.strip():
            raise ValueError(
                "No text could be read from this PDF -- it looks like a scanned or "
                "image-only document. OCR is not supported yet, so enter this "
                "intelligence manually."
            )
        return text

    raise ValueError(
        f"Unsupported file type '.{ext}'. Upload a .txt, .eml, .docx or .pdf file "
        "with a text layer."
    )


_EXTRACT_INSTRUCTIONS = """\
You are helping a UK police officer complete an intelligence submission for a \
Counter Corruption Unit case management system, from a document they have \
uploaded (an email, report or similar). Read the document text below and \
propose values for the intelligence record fields.

Respond with ONLY a single JSON object, no other text, no markdown code \
fences, with exactly these keys:
{
  "subject_code": string -- the subject officer's collar number/code exactly \
as it appears (e.g. "PC 1234"), or "" if none is stated,
  "source": one of SOURCE_TYPES_PLACEHOLDER,
  "category": one of CATEGORIES_PLACEHOLDER,
  "source_evaluation": one of SOURCE_EVAL_PLACEHOLDER,
  "intelligence_evaluation": one of INTEL_EVAL_PLACEHOLDER,
  "handling_code": one of HANDLING_CODES_PLACEHOLDER,
  "handling_conditions": one of HANDLING_CONDITIONS_PLACEHOLDER -- "None" \
unless the text clearly implies covert handling is needed,
  "crime_ref": string -- a linked crime/incident reference exactly as \
stated, or "" if none,
  "summary": string -- a prose summary of what, when, where, why, who, how, \
drawn ONLY from the document text,
  "sanitised": string -- a sanitised rewording of the same intelligence, \
suitable for wider dissemination outside this unit: keep the substantive \
facts but remove or generalise anything that would identify the source or \
reveal exactly how the information was obtained (e.g. reword a specific \
informant description as something generic like "information received"); \
never invent facts to fill this in
}

Rules: never invent a fact, name, date or reference not present in the \
text. Do not name any person in "summary" beyond what the document itself \
already states in the clear. If unsure of a controlled-vocabulary value, \
make your best reasonable estimate from the wording rather than leaving it \
blank, unless truly nothing in the text bears on it."""


def _build_prompt(text: str) -> str:
    instructions = (
        _EXTRACT_INSTRUCTIONS
        .replace("SOURCE_TYPES_PLACEHOLDER", str(taxonomy.SOURCE_TYPES))
        .replace("CATEGORIES_PLACEHOLDER", str(taxonomy.INTEL_CATEGORIES))
        .replace("SOURCE_EVAL_PLACEHOLDER", str(taxonomy.SOURCE_EVALUATION))
        .replace("INTEL_EVAL_PLACEHOLDER", str(taxonomy.INTELLIGENCE_EVALUATION))
        .replace("HANDLING_CODES_PLACEHOLDER", str(taxonomy.HANDLING_CODES))
        .replace("HANDLING_CONDITIONS_PLACEHOLDER", str(taxonomy.HANDLING_CONDITIONS))
    )
    return instructions + "\n\nDOCUMENT TEXT:\n" + text


_BLANK_DRAFT = {
    "subject_code": "", "source": "", "category": "", "source_evaluation": "",
    "intelligence_evaluation": "", "handling_code": "", "handling_conditions": "",
    "crime_ref": "", "summary": "", "sanitised": "",
}


async def draft_intel_from_text(text: str) -> dict:
    truncated = text.strip()[:MAX_CHARS]

    if llm.mode() != "live":
        # No model to extract with. A generic prompt echo (llm.py's usual
        # mock) would be useless here -- there's nothing to parse into
        # fields -- so instead surface the actual extracted text and leave
        # every field for the officer to fill in themselves. The workflow
        # (upload -> review -> save) is still fully exercisable offline.
        preview = truncated[:800] + ("..." if len(truncated) > 800 else "")
        return {
            **_BLANK_DRAFT,
            "summary": (
                "[OFFLINE PLACEHOLDER -- no AI provider configured. "
                "AI extraction did not run; this is the raw text read from the "
                "uploaded file for you to summarise yourself.]\n\n" + preview
            ),
            "llm_mode": "mock",
            "warning": None,
        }

    result = await llm.chat(_build_prompt(truncated), max_output_tokens=1200)
    raw = result.text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {
            **_BLANK_DRAFT,
            "llm_mode": "live",
            "warning": "The AI's response could not be read as a structured draft. Enter the fields manually.",
        }

    def pick(key, options):
        val = data.get(key)
        return val if val in options else ""

    return {
        "subject_code": str(data.get("subject_code") or "")[:64],
        "source": pick("source", taxonomy.SOURCE_TYPES),
        "category": pick("category", taxonomy.INTEL_CATEGORIES),
        "source_evaluation": pick("source_evaluation", taxonomy.SOURCE_EVALUATION),
        "intelligence_evaluation": pick("intelligence_evaluation", taxonomy.INTELLIGENCE_EVALUATION),
        "handling_code": pick("handling_code", taxonomy.HANDLING_CODES),
        "handling_conditions": pick("handling_conditions", taxonomy.HANDLING_CONDITIONS),
        "crime_ref": str(data.get("crime_ref") or "")[:128],
        "summary": str(data.get("summary") or ""),
        "sanitised": str(data.get("sanitised") or ""),
        "llm_mode": "live",
        "warning": None,
    }
