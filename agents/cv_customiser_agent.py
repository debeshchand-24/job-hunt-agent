import json
import re
import time
from datetime import datetime
from pathlib import Path

import anthropic
from loguru import logger

from config.settings import ANTHROPIC_API_KEY
from utils.docs_client import DocsClient
from utils.sheets_client import SheetsClient

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"

_SYSTEM_PROMPT = (
    "You are an expert career coach and CV writer specialising in product management "
    "roles in the Indian tech industry. You give specific, actionable CV customisation "
    "advice that helps candidates get shortlisted without misrepresenting their experience. "
    "You understand ATS systems and recruiter psychology."
)


def _build_user_prompt(
    company: str,
    role: str,
    score: int,
    tier: str,
    extracted_json: str,
    strong_areas: list,
    weak_areas: list,
    absent_areas: list,
    cv_version: str,
    cv_text: str,
) -> str:
    return f"""\
A product manager is applying for this role and needs specific CV customisation suggestions.

CRITICAL FORMATTING RULES (non-negotiable):
- For every reframed bullet point:
  * Count the EXACT character length of the original statement
    (including spaces, numbers, punctuation)
  * Your suggested rewrite MUST NOT exceed original length + 10 characters
  * Show the character count for both:
    CURRENT (87 chars): 'Led product for checkout flow...'
    SUGGESTED (92 chars): 'Owned checkout product driving...'
  * If you cannot reframe within this limit:
    suggest KEYWORD INSERTION instead of rewrite — add 1-2 keywords into
    the existing sentence without restructuring it
  * Never sacrifice meaning to fit the limit — if it cannot be done
    within the character limit, say:
    'CHARACTER LIMIT: Cannot reframe within limit. Suggest keyword insertion only.'

- For the Professional Summary section:
  * Same rule applies — rewrite must be within original summary length + 20 chars
    (summaries need slightly more flexibility than bullet points)

- Show a character count check for every reframed statement. No exceptions.

IMPORTANT RULES:
- Never suggest fabricating experience
- Reframe truthfully — same achievement, better framing for this JD
- Use keywords from JD naturally, not stuffed
- Prioritise changes by impact on shortlisting
- Preserve the candidate's voice and style

JOB DETAILS:
Company: {company}
Role: {role}
Match Score: {score}/100 | Tier: {tier}

EXTRACTED JD REQUIREMENTS:
{extracted_json}

MATCH ANALYSIS:
Strong areas: {json.dumps(strong_areas)}
Weak areas: {json.dumps(weak_areas)}
Absent skills: {json.dumps(absent_areas)}

CANDIDATE'S CURRENT CV ({cv_version} version):
{cv_text}

Produce a structured customisation report with these exact sections:

## MATCH SUMMARY
Quick 3-line summary of fit and strategy

## PROFESSIONAL SUMMARY
Current text → Suggested rewrite → Reason

## EXPERIENCE SECTIONS
For each relevant role:
- Points to reorder (specify which → where)
- Points to reframe (current → suggested → reason)
- New points to add from existing CV (with source role/company)
- Points to de-emphasise for this role

## SKILLS SECTION
- Keywords to add (from JD, genuinely applicable)
- Keywords to reorder to top

## OTHER SECTIONS
- Any changes to education/certifications section
- LinkedIn headline suggestion for this application

## CHANGE PRIORITY
List all changes in priority order:
HIGH IMPACT (do these first)
MEDIUM IMPACT
LOW IMPACT (optional)

## SECTIONS NEEDING REVIEW
Checkbox list of all sections with changes

Format everything clearly with headers.
Be specific — quote exact current text and suggest exact replacement text."""


# Ordered substitutions applied before splitting into words (most specific first)
_ROLE_SUBS = [
    (r"\bassociate product manager\b",  "APM"),
    (r"\bsenior product manager\b",     "Sr PM"),
    (r"\bproduct management\s*[-–]\s*", ""),   # strip the "Product Management -" prefix
    (r"\bproduct manager\b",            "PM"),
    (r"\bsenior\b",                     "Sr"),
]


def _shorten_role(role: str, company: str, score) -> str:
    """Build a tab title: '{Company} | {shortened role} | {score}', max 50 chars."""
    s = role.strip()

    for pattern, replacement in _ROLE_SUBS:
        s = re.sub(pattern, replacement, s, flags=re.IGNORECASE).strip()

    # Strip punctuation noise: parens, dashes, ampersands
    s = re.sub(r"[()&\-–]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    words = s.split()
    short = " ".join(words[:4]) if len(words) > 4 else s

    title = f"{company} | {short} | {score}"
    if len(title) <= 50:
        return title

    # Fall back to 3 words + ellipsis
    short3 = " ".join(words[:3]) + "..."
    title  = f"{company} | {short3} | {score}"
    return title[:50]


class CVCustomiserAgent:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.model  = "claude-sonnet-4-20250514"
        self.sheets = SheetsClient()
        self.docs   = DocsClient()
        self._load_content()
        logger.info("CVCustomiserAgent initialised")

    # ------------------------------------------------------------------ #
    # Content loading
    # ------------------------------------------------------------------ #

    def _load_content(self):
        self.growth_cv = self._load_file(_DATA_DIR / "growth_cv_text.txt", "growth CV")
        self.ai_cv     = self._load_file(_DATA_DIR / "ai_cv_text.txt",     "AI CV")

    def _load_file(self, path: Path, label: str) -> str:
        if not path.exists():
            logger.warning(f"{label} not found at {path} — using empty string")
            return ""
        text = path.read_text(encoding="utf-8")
        logger.info(f"Loaded {label}: {path.name} ({len(text):,} chars)")
        return text

    # ------------------------------------------------------------------ #
    # Sheet reading
    # ------------------------------------------------------------------ #

    _TOP_N = 3

    def get_jobs_to_customise(self) -> list[dict]:
        rows = self.sheets.get_all_rows()
        # trigger_cv_customisation is not a sheet column; status="matched"
        # already encodes that the threshold was met.
        eligible = [
            r for r in rows
            if r.get("status") == "matched"
            and not r.get("doc_tab_url", "").strip()
        ]

        # Sort by match_score descending; treat missing/non-numeric scores as 0
        eligible.sort(
            key=lambda r: int(r.get("match_score") or 0),
            reverse=True,
        )

        top = eligible[: self._TOP_N]

        if not eligible:
            logger.info("Found 0 eligible jobs needing CV customisation")
            return []

        lines = [f"Found {len(eligible)} eligible jobs, processing top {self._TOP_N} by match score:"]
        for i, job in enumerate(top, start=1):
            lines.append(
                f"  {i}. {job.get('role', '?')} at {job.get('company', '?')} "
                f"— {job.get('match_score', '?')}"
            )
        logger.info("\n".join(lines))
        return top

    # ------------------------------------------------------------------ #
    # CV selection
    # ------------------------------------------------------------------ #

    def select_cv(self, job: dict) -> tuple[str, str]:
        version = (job.get("cv_version") or "growth").lower()
        if version == "ai":
            return self.ai_cv, "ai"
        return self.growth_cv, "growth"

    # ------------------------------------------------------------------ #
    # Suggestion generation
    # ------------------------------------------------------------------ #

    def generate_suggestions(self, job: dict) -> str:
        role    = job.get("role", "?")
        company = job.get("company", "?")
        score   = int(job.get("match_score", 0))
        tier    = job.get("match_tier", "?")

        # Parse structured fields
        try:
            extracted = json.loads(job.get("extracted_skills", "") or "{}")
        except json.JSONDecodeError:
            extracted = {}

        try:
            strong_areas = json.loads(job.get("strong_areas", "") or "[]")
        except json.JSONDecodeError:
            strong_areas = []

        try:
            weak_areas = json.loads(job.get("weak_areas", "") or "[]")
        except json.JSONDecodeError:
            weak_areas = []

        absent_areas = extracted.get("absent_areas", [])
        cv_text, cv_version = self.select_cv(job)

        user_prompt = _build_user_prompt(
            company        = company,
            role           = role,
            score          = score,
            tier           = tier,
            extracted_json = json.dumps(extracted, indent=2),
            strong_areas   = strong_areas,
            weak_areas     = weak_areas,
            absent_areas   = absent_areas,
            cv_version     = cv_version,
            cv_text        = cv_text,
        )

        response = self.client.messages.create(
            model      = self.model,
            max_tokens = 4096,
            system     = _SYSTEM_PROMPT,
            messages   = [{"role": "user", "content": user_prompt}],
        )

        usage = response.usage
        logger.info(
            f"  Suggestions generated — tokens in={usage.input_tokens} "
            f"out={usage.output_tokens}"
        )
        return response.content[0].text.strip()

    # ------------------------------------------------------------------ #
    # Doc writing
    # ------------------------------------------------------------------ #

    def write_to_doc(self, job: dict, suggestions: str) -> str:
        company   = job.get("company", "?")
        role      = job.get("role", "?")
        score     = job.get("match_score", "?")
        tier      = job.get("match_tier", "?")
        cv_ver    = job.get("cv_version", "growth")
        job_url   = job.get("url", "")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

        tab_title = _shorten_role(role, company, score)

        header = (
            f"Company:   {company}\n"
            f"Role:      {role}\n"
            f"Score:     {score}/100 | {tier} match\n"
            f"CV used:   {cv_ver}\n"
            f"Job URL:   {job_url}\n"
            f"Generated: {timestamp}\n"
            f"{'─' * 60}\n\n"
        )

        full_content = header + suggestions

        try:
            tab_id  = self.docs.create_tab(tab_title)
            self.docs.write_to_tab(tab_id, full_content)
            tab_url = self.docs.get_doc_url(tab_id)
            logger.info(f"Written to doc tab: {tab_url}")
            return tab_url
        except Exception as exc:
            logger.error(f"Google Doc write failed: {exc} — saving locally")
            return self._save_local_fallback(job.get("job_id", "unknown"), full_content)

    def _save_local_fallback(self, job_id: str, content: str) -> str:
        _LOGS_DIR.mkdir(parents=True, exist_ok=True)
        path = _LOGS_DIR / f"{job_id}_suggestions.txt"
        path.write_text(content, encoding="utf-8")
        logger.info(f"Suggestions saved locally: {path}")
        return f"local://{path}"

    # ------------------------------------------------------------------ #
    # Sheet writing
    # ------------------------------------------------------------------ #

    def update_sheet(self, job_id: str, doc_tab_url: str):
        self.sheets.batch_update_row(job_id, {
            "doc_tab_url": doc_tab_url,
            "status":      "cv_ready",
        })
        logger.info(f"CV suggestions written for {job_id}")

    # ------------------------------------------------------------------ #
    # Entry point
    # ------------------------------------------------------------------ #

    def run(self):
        jobs = self.get_jobs_to_customise()
        if not jobs:
            logger.info("No jobs need CV customisation")
            return

        total    = len(jobs)
        doc_base = f"https://docs.google.com/document/d/{self.docs._doc_id}/edit"

        for i, job in enumerate(jobs, start=1):
            role    = job.get("role", "?")
            company = job.get("company", "?")
            score   = job.get("match_score", "?")
            job_id  = job.get("job_id", "")

            logger.info(
                f"Customising CV for {i}/{total}: {role} at {company} (score: {score})"
            )

            try:
                suggestions = self.generate_suggestions(job)
                tab_url     = self.write_to_doc(job, suggestions)
                self.update_sheet(job_id, tab_url)
            except Exception as exc:
                logger.error(f"  Failed for {role} at {company}: {exc}")

            time.sleep(5)

        logger.info(
            f"CV customisation complete: {total}/{self._TOP_N} jobs processed "
            f"(limited to top {self._TOP_N} by match score)\n"
            f"Google Doc: {doc_base}\n"
            f"To process more: update limit in get_jobs_to_customise()"
        )
