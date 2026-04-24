"""
parser.py — AI-Powered Planning Document Parser (Claude Code version)
Uses the 'claude' CLI (Claude Code) instead of direct API calls.
No API key required — uses your existing Claude Code authentication.
"""

import json
import logging
import subprocess
import pdfplumber
from pathlib import Path
from config import JSON_DIR, MIN_SIGNAL_SCORE

log = logging.getLogger(__name__)

PROMPT_TEMPLATE = """You are a commercial real estate development intelligence analyst.
Read this Utah city planning commission or city council meeting document and extract
structured development signals that indicate growth and opportunity.

Respond ONLY with valid JSON — no explanation, no markdown fences, no preamble.

Extract every item in these categories:
- REZONE: Land being rezoned (especially to commercial, industrial, mixed-use)
- NEW_SUBDIVISION: Residential subdivisions being approved (rooftop growth signal)
- COMMERCIAL_PROJECT: New commercial, retail, office, or industrial proposals
- MINIFLEX_OPPORTUNITY: Light industrial, flex space, contractor condo, storage, gyms, studios
- INFRASTRUCTURE: New roads, intersections, utility extensions
- ANNEXATION: Land being annexed into city limits
- GENERAL_PLAN_AMENDMENT: Future land use map or general plan changes
- LARGE_PROJECT: Any large development (50+ units or 5+ acres)
- DEVELOPER_ACTIVITY: Named developers or investors appearing in the market

For each signal:
{{
  "signal_type": "one of the types above",
  "description": "plain English description of what is happening",
  "location": "address or cross streets as stated in document",
  "acres": null or number,
  "units": null or number,
  "developer": null or "name if mentioned",
  "zoning_from": null or "existing zone",
  "zoning_to": null or "proposed zone",
  "status": "PROPOSED | APPROVED | DENIED | TABLED | CONTINUED",
  "growth_score": 0-100,
  "notes": "any useful context for a real estate developer"
}}

Return exactly this structure:
{{
  "signals": [...],
  "summary": "2-3 sentence plain English summary of development activity",
  "top_areas": ["specific intersections or areas with most activity"]
}}

If no relevant content: {{"signals": [], "summary": "No significant development activity found.", "top_areas": []}}

--- DOCUMENT ---
City: {city}
County: {county}
Body: {body_type}
Date: {event_date}
Title: {title}

{text}
--- END DOCUMENT ---"""


def check_claude_cli() -> bool:
    """Verify that the claude CLI is available and working."""
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            log.info(f"Claude Code found: {result.stdout.strip()}")
            return True
        else:
            log.error("claude CLI found but returned an error")
            return False
    except FileNotFoundError:
        log.error("claude CLI not found. Is Claude Code installed?")
        log.error("Install from: https://claude.ai/code")
        return False
    except Exception as e:
        log.error(f"Error checking claude CLI: {e}")
        return False


def extract_text_from_pdf(pdf_path: str, max_pages: int = 30) -> str:
    """Extract text from a PDF using pdfplumber."""
    try:
        text_parts = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages[:max_pages]:
                text = page.extract_text()
                if text:
                    text_parts.append(text)
        full_text = "\n\n".join(text_parts)
        return full_text[:12000]  # cap to keep prompts reasonable
    except Exception as e:
        log.warning(f"PDF text extraction failed ({pdf_path}): {e}")
        return ""


def call_claude_cli(prompt: str, timeout: int = 300) -> str | None:
    """
    Call the claude CLI with a prompt and return the response text.
    Uses 'claude -p' for non-interactive (print) mode.
    """
    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if result.returncode != 0:
            log.warning(f"Claude CLI error: {result.stderr[:200]}")
            return None
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        log.warning("Claude CLI timed out")
        return None
    except Exception as e:
        log.error(f"Claude CLI call failed: {e}")
        return None


def parse_document(notice: dict, pdf_path: str) -> dict | None:
    """
    Parse a single planning document using Claude Code CLI.
    Returns structured signal dict or None on failure.
    """
    text = extract_text_from_pdf(pdf_path)
    if len(text) < 100:
        log.warning(f"Too little text in {pdf_path}, skipping")
        return None

    prompt = PROMPT_TEMPLATE.format(
        city=notice["city"],
        county=notice["county"],
        body_type=notice["body_type"],
        event_date=notice["event_date"],
        title=notice["title"],
        text=text
    )

    raw = call_claude_cli(prompt)
    if not raw:
        return None

    # Strip markdown fences if Claude adds them anyway
    if "```" in raw:
        parts = raw.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                raw = part
                break

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # Try to find JSON block within response
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                parsed = json.loads(raw[start:end])
            except json.JSONDecodeError as e:
                log.error(f"JSON parse failed for {pdf_path}: {e}")
                return None
        else:
            log.error(f"No JSON found in response for {pdf_path}")
            return None

    # Filter low-scoring signals
    parsed["signals"] = [
        s for s in parsed.get("signals", [])
        if s.get("growth_score", 0) >= MIN_SIGNAL_SCORE
    ]

    # Attach source metadata
    parsed["source"] = {
        "city":       notice["city"],
        "county":     notice["county"],
        "body_type":  notice["body_type"],
        "event_date": notice["event_date"],
        "title":      notice["title"],
        "notice_url": notice.get("notice_url", ""),
        "pdf_path":   pdf_path,
    }

    return parsed


def parse_all_notices(notices: list[dict]) -> list[dict]:
    """
    Run Claude Code parsing on all downloaded PDFs.
    Returns list of parsed signal documents.
    """
    if not check_claude_cli():
        log.error("Cannot proceed — claude CLI not available.")
        log.error("Make sure Claude Code is installed and you are logged in.")
        return []

    total_pdfs = sum(len(n["pdfs"]) for n in notices)
    log.info(f"Starting AI parsing — {total_pdfs} PDFs across {len(notices)} notices")

    results   = []
    processed = 0

    for notice in notices:
        for pdf in notice["pdfs"]:
            local_path = pdf.get("local_path")
            if not local_path or not Path(local_path).exists():
                continue

            # Check for cached result
            cache_key  = Path(local_path).stem
            cache_path = Path(JSON_DIR) / f"{cache_key}.json"

            if cache_path.exists():
                log.debug(f"Loading cached: {cache_path.name}")
                with open(cache_path) as f:
                    results.append(json.load(f))
                processed += 1
                continue

            log.info(f"Parsing [{processed+1}/{total_pdfs}]: {pdf['name']} ({notice['city']})")
            result = parse_document(notice, local_path)

            if result:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                with open(cache_path, "w") as f:
                    json.dump(result, f, indent=2)
                results.append(result)
                sig_count = len(result.get("signals", []))
                log.info(f"  → {sig_count} signals extracted")
            else:
                log.warning(f"  → No result for {pdf['name']}")

            processed += 1

    log.info(f"Parsing complete. {len(results)} documents parsed.")
    return results


if __name__ == "__main__":
    import glob
    pdfs = glob.glob("data/pdfs/**/*.pdf", recursive=True)
    if pdfs:
        test_notice = {
            "city":       "Test City",
            "county":     "Test County",
            "body_type":  "Planning Commission",
            "event_date": "2026-01-01",
            "title":      "Test Document",
            "notice_url": ""
        }
        result = parse_document(test_notice, pdfs[0])
        print(json.dumps(result, indent=2))
    else:
        print("No PDFs found. Run scraper.py first.")
