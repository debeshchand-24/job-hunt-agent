import json
import time

import anthropic
from loguru import logger

from config.settings import ANTHROPIC_API_KEY
from utils.sheets_client import SheetsClient

_SYSTEM_PROMPT = (
    "You are an expert HR analyst who extracts structured information from job "
    "descriptions with high accuracy. Always respond with valid JSON only, no other text."
)

_USER_PROMPT_TEMPLATE = """\
Extract the following from this job description.
Return ONLY a valid JSON object, no markdown, no explanation, just the raw JSON.

{{
  "required_experience_years": <number or string range>,
  "required_skills": [<list of must-have skills>],
  "preferred_skills": [<list of nice-to-have skills>],
  "domain": <industry e.g. fintech, ecommerce, saas>,
  "seniority_level": <APM/PM/SPM/Lead/Director/VP>,
  "role_type": <growth/platform/consumer/b2b/data/other>,
  "key_responsibilities": [<top 5 as short phrases>],
  "education_required": <degree if mentioned, else null>,
  "location": <city or remote>,
  "tools_mentioned": [<any tools, software mentioned>],
  "unique_requirements": <unusual requirements if any>
}}

Job Description:
{jd_raw}"""


class ExtractorAgent:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.model  = "claude-haiku-4-5-20251001"
        self.sheets = SheetsClient()
        logger.info(f"ExtractorAgent initialised (model={self.model})")

    # ------------------------------------------------------------------ #
    # Sheet reading
    # ------------------------------------------------------------------ #

    def get_unprocessed_jobs(self) -> list[dict]:
        rows = self.sheets.get_all_rows()
        unprocessed = [
            r for r in rows
            if r.get("status") == "new"
            and len(r.get("jd_raw", "")) > 200
            and not r.get("extracted_skills", "").strip()
        ]
        logger.info(f"Found {len(unprocessed)} unprocessed jobs")
        return unprocessed

    # ------------------------------------------------------------------ #
    # Extraction
    # ------------------------------------------------------------------ #

    def extract_jd(self, job: dict) -> dict:
        jd_raw = job.get("jd_raw", "")
        user_prompt = _USER_PROMPT_TEMPLATE.format(jd_raw=jd_raw)

        response = self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw_text = response.content[0].text.strip()
        usage = {
            "input_tokens":  response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }

        # Strip markdown code fences Claude sometimes adds despite instructions
        clean = raw_text
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[-1]          # drop first ```[json] line
            clean = clean.rsplit("```", 1)[0].strip()  # drop trailing ```

        try:
            extracted = json.loads(clean)
        except json.JSONDecodeError as exc:
            logger.error(
                f"JSON parse failed for {job.get('role')} at {job.get('company')}: {exc}"
            )
            extracted = {"_raw_response": raw_text, "_parse_error": str(exc)}

        # Attach usage under a private key so callers can log it;
        # update_sheet() will strip this before writing to the sheet.
        extracted["_usage"] = usage
        return extracted

    # ------------------------------------------------------------------ #
    # Sheet writing
    # ------------------------------------------------------------------ #

    def update_sheet(self, job_id: str, extracted: dict):
        # Strip internal metadata before persisting
        payload = {k: v for k, v in extracted.items() if not k.startswith("_")}
        self.sheets.update_row(job_id, "extracted_skills", json.dumps(payload))
        self.sheets.update_row(job_id, "status", "extracted")

    # ------------------------------------------------------------------ #
    # Entry point
    # ------------------------------------------------------------------ #

    def run(self):
        jobs = self.get_unprocessed_jobs()
        if not jobs:
            logger.info("No unprocessed jobs found — nothing to do.")
            return

        total = len(jobs)
        for i, job in enumerate(jobs, start=1):
            role    = job.get("role", "?")
            company = job.get("company", "?")
            job_id  = job.get("job_id", "")

            logger.info(f"Extracting {i}/{total}: {role} at {company}")
            extracted = self.extract_jd(job)

            usage = extracted.get("_usage", {})
            logger.info(
                f"  tokens — in: {usage.get('input_tokens', '?')}, "
                f"out: {usage.get('output_tokens', '?')}"
            )

            self.update_sheet(job_id, extracted)
            time.sleep(2)

        logger.info(f"Extraction complete: {total} jobs processed")
