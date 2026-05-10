import base64
import json
import logging
import time
from pathlib import Path

import anthropic
from loguru import logger

# pdfplumber uses pdfminer internally which emits noisy font-descriptor warnings
logging.getLogger("pdfminer").setLevel(logging.ERROR)

from config.settings import ANTHROPIC_API_KEY
from utils.sheets_client import SheetsClient

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

_SYSTEM_PROMPT = (
    "You are an expert talent matcher and career coach with deep knowledge of the Indian "
    "tech industry. You evaluate candidate-job fit with nuance, understanding that adjacent "
    "skills and domains transfer well. You are evaluating a senior product manager profile. "
    "Always respond with valid JSON only, no markdown, no explanation."
)

# {cv_description}, {extracted_json}, {cv_name} are substituted at call time.
# Double braces {{ }} become literal { } after .format().
_SCORING_PROMPT = """\
The {cv_description} contains the candidate's background and qualifications.

Score this candidate against the job using the exact rubric below. Be generous with adjacent \
skills and domain transfers — a fintech PM can absolutely succeed in ecommerce.

CANDIDATE CONTEXT:
- 10+ years total, 8+ product experience
- Targets Group PM/Principal PM/Director at startups
- Targets SPM/Lead PM at big tech
- Preferred: Growth, Consumer, Platform roles

JOB EXTRACTED DATA:
{extracted_json}

SCORING RUBRIC:
Skills (30pts): exact match=full, adjacent=0.5 credit
Experience (20pts) - INVERTED U CURVE. Overqualification is penalised just as
  underqualification is - recruiters reject candidates who are too senior for a role
  as often as those who are too junior. Candidate has 10+ overall, 8+ product yrs.
  Underqualified: gap 3+yrs below req=4pts, gap 1-2yrs=10pts, meets exactly=16pts
  Right fit:      exceeds by 1-3yrs=20pts (sweet spot), exceeds by 4-5yrs=14pts
  Overqualified:  exceeds by 6-7yrs=8pts, exceeds by 8+yrs=2pts
  Example: APM req 0-2yrs experience → candidate scores 2pts (10yr gap).
           SPM req 6-8yrs → candidate scores 20pts (sweet spot).
           Director req 12+yrs → candidate scores 10pts (1-2yr gap below).
Role type (15pts): exact=15, adjacent=10, transferable=6, stretch=2
Domain (15pts): same=15, adjacent=11, similar behaviour=7, different=3
Seniority (10pts): exact for company type=10, 1 level off=6, 2 levels off=2
Leadership scope (5pts) - READ JD CAREFULLY FIRST:
  Step 1: Does the JD explicitly mention managing PMs, leading a team, or people management?
  Step 2a: If NO mention of leadership in JD → score 5/5 (never penalise absence)
  Step 2b: If YES mentioned in JD, check CV for explicit evidence:
    managing PMs, team size stated, hiring, perf reviews, org building → 5/5
    vague or implicit evidence only → 3/5
    no evidence in CV → 1/5
  Never auto-score 5/5 without checking the JD first.
Others/conditional (10pts): auto 10 if not mentioned, 10 if mentioned+met, 0 if mentioned+not met

THRESHOLDS: 85+=strong, 70-84=good, 55-69=moderate, below 55=weak

Return ONLY this exact JSON structure, no markdown:
{{
  "total_score": <0-100>,
  "skills_score": <0-30>,
  "experience_score": <0-20>,
  "role_type_score": <0-15>,
  "domain_score": <0-15>,
  "seniority_score": <0-10>,
  "leadership_score": <0-5>,
  "leadership_reasoning": "<one sentence: was leadership mentioned in JD? what evidence in CV?>",
  "others_score": <0-10>,
  "match_tier": "<strong/good/moderate/weak>",
  "strong_areas": [<3-4 specific strengths as strings>],
  "weak_areas": [<2-3 gaps or concerns as strings>],
  "absent_areas": [<skills completely missing as strings>],
  "reasoning": "<2-3 sentence overall assessment>",
  "apply_recommended": <true/false>,
  "trigger_cv_customisation": <true/false>,
  "which_cv_used": "{cv_name}"
}}"""

_GROWTH_ROLE_KEYWORDS = {"growth", "retention", "acquisition", "consumer"}
_AI_ROLE_KEYWORDS     = {"platform", "ai", "ml", "data", "technical"}


class MatchingAgent:
    def __init__(self):
        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.model  = "claude-sonnet-4-20250514"
        self.sheets = SheetsClient()
        self._load_cvs()
        self._load_candidate_profile()
        logger.info(f"MatchingAgent initialised (model={self.model})")

    # ------------------------------------------------------------------ #
    # CV loading
    # ------------------------------------------------------------------ #

    def load_pdf_as_base64(self, path: str) -> str:
        return base64.standard_b64encode(Path(path).read_bytes()).decode("utf-8")

    def extract_cv_text(self, pdf_path: str) -> str:
        """Extract plain text from a PDF using pdfplumber.

        Saves the result alongside the PDF as *_text.txt so subsequent
        loads skip re-extraction. Returns the extracted text string.
        """
        import pdfplumber

        path = Path(pdf_path)
        with pdfplumber.open(pdf_path) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
        text = "\n".join(pages).strip()

        # Cache next to the PDF so re-runs don't re-extract
        cache_path = path.with_name(path.stem + "_text.txt")
        cache_path.write_text(text, encoding="utf-8")

        logger.info(f"Extracted {len(text):,} characters from {path.name}")
        return text

    def _load_cv_version(self, version: str) -> dict:
        """Load a CV version as text, always preserving pdf_path for the A4 agent.

        Priority:
          1. <version>_cv_text.txt — pre-extracted text (preferred, cheapest)
          2. <version>_cv.pdf      — extract text on the fly and cache it
          3. <version>_cv.txt      — manually written text fallback
          (growth only) my_cv.txt  — last-resort generic fallback
        """
        if version == "growth":
            candidates = [
                ("growth_cv_text.txt", "text"),
                ("growth_cv.pdf",      "pdf"),
                ("growth_cv.txt",      "text"),
                ("my_cv.txt",          "text"),
            ]
            companion_pdf_name = "growth_cv.pdf"
        else:
            candidates = [
                ("ai_cv_text.txt", "text"),
                ("ai_cv.pdf",      "pdf"),
                ("ai_cv.txt",      "text"),
            ]
            companion_pdf_name = "ai_cv.pdf"

        for filename, ftype in candidates:
            path = _DATA_DIR / filename
            if not path.exists():
                continue

            if ftype == "text":
                content  = path.read_text(encoding="utf-8")
                # Look for the companion PDF so A4 can use it directly
                pdf_candidate = _DATA_DIR / companion_pdf_name
                pdf_path = str(pdf_candidate) if pdf_candidate.exists() else None
                logger.info(
                    f"{version.title()} CV loaded from: {filename} "
                    f"({len(content):,} chars) - text mode - cost optimised"
                )
                return {"type": "text", "content": content, "pdf_path": pdf_path}

            else:  # pdf
                cache = path.with_name(path.stem + "_text.txt")
                if cache.exists():
                    content = cache.read_text(encoding="utf-8")
                else:
                    content = self.extract_cv_text(str(path))
                logger.info(
                    f"{version.title()} CV loaded from: {filename} "
                    f"({path.stat().st_size // 1024}KB PDF → {len(content):,} chars extracted)"
                    f" - text mode - cost optimised"
                )
                return {"type": "text", "content": content, "pdf_path": str(path)}

        # AI-specific fallback: reuse the already-loaded growth CV
        if version == "ai":
            logger.warning("AI CV not found — falling back to growth CV")
            return self.growth_cv

        raise FileNotFoundError(
            "No growth CV file found. Run setup_cvs.py for instructions."
        )

    def _load_cvs(self):
        # Growth must load first so AI can fall back to it
        self.growth_cv = self._load_cv_version("growth")
        self.ai_cv     = self._load_cv_version("ai")

    def _load_candidate_profile(self):
        profile_path = _DATA_DIR / "candidate_profile.json"
        if profile_path.exists() and profile_path.stat().st_size > 2:
            with open(profile_path, encoding="utf-8") as f:
                self.candidate_profile = json.load(f)
            profile_chars = len(json.dumps(self.candidate_profile))
            self.use_profile = True
            logger.info(f"Using pre-computed candidate profile ({profile_chars:,} chars)")
        else:
            self.candidate_profile = {}
            self.use_profile = False
            logger.warning(
                "No candidate profile found. Run setup_candidate_profile.py first. "
                "Falling back to full CV text."
            )

    # ------------------------------------------------------------------ #
    # CV selection
    # ------------------------------------------------------------------ #

    def select_cv(self, extracted_job: dict) -> tuple:
        role_type = (extracted_job.get("role_type") or "").lower()
        company   = extracted_job.get("company", "?")

        if any(kw in role_type for kw in _AI_ROLE_KEYWORDS):
            cv, cv_name = self.ai_cv, "ai"
        else:
            cv, cv_name = self.growth_cv, "growth"

        logger.info(f"Selected {cv_name} CV for {role_type!r} role at {company}")
        return cv, cv_name

    # ------------------------------------------------------------------ #
    # Sheet reading
    # ------------------------------------------------------------------ #

    def get_jobs_to_match(self) -> list:
        rows = self.sheets.get_all_rows()
        ready = [r for r in rows if r.get("status") == "extracted"]
        logger.info(f"Found {len(ready)} jobs ready for matching")
        return ready

    # ------------------------------------------------------------------ #
    # Matching
    # ------------------------------------------------------------------ #

    def match_job(self, job: dict) -> dict:
        role    = job.get("role", "?")
        company = job.get("company", "?")

        # Parse extracted skills JSON
        raw_extracted = job.get("extracted_skills", "")
        try:
            extracted_data = json.loads(raw_extracted) if raw_extracted else {}
        except json.JSONDecodeError as exc:
            logger.error(f"Malformed extracted_skills JSON for {role} at {company}: {exc}")
            return {}

        extracted_data["company"] = company  # give Claude the company name for context

        cv, cv_name = self.select_cv(extracted_data)
        extracted_json = json.dumps(extracted_data, indent=2)

        if self.use_profile:
            # ── Profile mode (preferred): structured JSON, ~75% fewer tokens ──
            profile_section = (
                "CANDIDATE PROFILE (structured — use these fields for all scoring):\n"
                + json.dumps(self.candidate_profile, indent=2)
                + "\n\n"
                "Use candidate_profile fields for scoring:\n"
                "- Skills match: compare required_skills against "
                "profile.skills.core_pm_skills + technical_skills\n"
                "- Experience: use profile.total_experience_years "
                "and product_experience_years\n"
                "- Domain: compare against profile.domains[].name and depth\n"
                "- Seniority: use profile.seniority_profile.current_level\n"
                "- Leadership: use profile.seniority_profile.leadership_evidence\n"
                "- Education/others: use profile.education.has_mba, tier, degree\n\n"
                "---\n\n"
            )
            scoring_text = _SCORING_PROMPT.format(
                cv_description="structured candidate profile above",
                extracted_json=extracted_json,
                cv_name=cv_name,
            )
            messages = [{
                "role": "user",
                "content": profile_section + scoring_text,
            }]
            match_mode = "profile"

        elif cv["type"] == "pdf":
            # ── Fallback: PDF as base64 document ──
            scoring_text = _SCORING_PROMPT.format(
                cv_description="PDF document above",
                extracted_json=extracted_json,
                cv_name=cv_name,
            )
            messages = [{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": cv["content"],
                        },
                    },
                    {"type": "text", "text": scoring_text},
                ],
            }]
            match_mode = "cv_pdf"

        else:
            # ── Fallback: plain CV text ──
            scoring_text = _SCORING_PROMPT.format(
                cv_description="text CV above",
                extracted_json=extracted_json,
                cv_name=cv_name,
            )
            messages = [{
                "role": "user",
                "content": f"CV TEXT:\n{cv['content']}\n\n---\n\n{scoring_text}",
            }]
            match_mode = "cv_text"

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1500,
                system=_SYSTEM_PROMPT,
                messages=messages,
            )
        except Exception as exc:
            logger.error(f"API call failed for {role} at {company}: {exc}")
            return {"_api_error": str(exc)}

        raw_text = response.content[0].text.strip()
        usage = {
            "input_tokens":  response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }

        # Strip markdown fences if present
        clean = raw_text
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[-1]
            clean = clean.rsplit("```", 1)[0].strip()

        try:
            result = json.loads(clean)
        except json.JSONDecodeError as exc:
            logger.error(f"JSON parse failed for {role} at {company}: {exc}")
            result = {"_raw_response": raw_text, "_parse_error": str(exc)}
            result["_usage"] = usage
            return result

        # Preserve Claude's raw decisions before overriding
        result["_claude_trigger"] = result.get("trigger_cv_customisation")
        result["_claude_apply"]   = result.get("apply_recommended")

        # Enforce deterministic tier + action rules — do not trust Claude's booleans
        total_score = result.get("total_score", 0)
        if total_score >= 85:
            result["match_tier"]               = "strong"
            result["trigger_cv_customisation"] = True
            result["apply_recommended"]        = True
            result["priority"]                 = True
        elif total_score >= 70:
            result["match_tier"]               = "good"
            result["trigger_cv_customisation"] = True
            result["apply_recommended"]        = True
            result["priority"]                 = False
        elif total_score >= 60:
            result["match_tier"]               = "ok"
            result["trigger_cv_customisation"] = True
            result["apply_recommended"]        = True
            result["priority"]                 = False
        else:
            result["match_tier"]               = "weak"
            result["trigger_cv_customisation"] = False
            result["apply_recommended"]        = False
            result["priority"]                 = False

        logger.info(
            f"Score {total_score}: tier={result['match_tier']} "
            f"cv_trigger={result['trigger_cv_customisation']} "
            f"priority={result['priority']} (threshold rules applied)"
        )

        result["_usage"]      = usage
        result["_match_mode"] = match_mode
        return result

    # ------------------------------------------------------------------ #
    # Sheet writing
    # ------------------------------------------------------------------ #

    def update_sheet(self, job_id: str, match_result: dict):
        total_score = match_result.get("total_score", 0)
        match_tier  = match_result.get("match_tier", "weak")
        status      = "matched" if match_tier in ("strong", "good", "ok") else "weak_match"
        self.sheets.batch_update_row(job_id, {
            "match_score":  str(total_score),
            "match_tier":   match_tier,
            "priority":     str(match_result.get("priority", False)),
            "strong_areas": json.dumps(match_result.get("strong_areas", [])),
            "weak_areas":   json.dumps(match_result.get("weak_areas", [])),
            "cv_version":   match_result.get("which_cv_used", ""),
            "status":       status,
        })
        logger.info(
            f"Updated sheet for job_id: {job_id} "
            f"score={total_score} tier={match_tier} status={status}"
        )

    # ------------------------------------------------------------------ #
    # Manual override
    # ------------------------------------------------------------------ #

    def trigger_manual_customisation(self, job_id: str):
        rows = self.sheets.get_all_rows()
        row  = next((r for r in rows if r.get("job_id") == job_id), None)
        if row is None:
            logger.warning(f"Job {job_id} not found in sheet")
            return
        current_status = row.get("status", "")
        if current_status != "weak_match":
            logger.warning(
                f"Job {job_id} is not in weak_match status - "
                f"current status: {current_status}"
            )
            return
        self.sheets.batch_update_row(job_id, {
            "status": "matched",
        })
        logger.info(f"Manual override: {job_id} approved for CV customisation")

    # ------------------------------------------------------------------ #
    # Entry point
    # ------------------------------------------------------------------ #

    def run(self):
        jobs = self.get_jobs_to_match()
        if not jobs:
            logger.info("No jobs ready for matching")
            return

        total      = len(jobs)
        tiers      = {"strong": 0, "good": 0, "ok": 0, "weak": 0}
        cv_trigger = 0

        for i, job in enumerate(jobs, start=1):
            role    = job.get("role", "?")
            company = job.get("company", "?")
            job_id  = job.get("job_id", "")

            logger.info(f"Matching {i}/{total}: {role} at {company}")

            match_result = self.match_job(job)

            if not match_result or "_api_error" in match_result:
                logger.error(f"  Skipping sheet update — match failed for {role}")
                try:
                    self.sheets.batch_update_row(job_id, {"status": "match_failed"})
                except Exception:
                    pass
                time.sleep(3)
                continue

            if "_parse_error" in match_result:
                logger.error(f"  Response not parseable for {role} — skipping")
                time.sleep(3)
                continue

            usage = match_result.get("_usage", {})
            logger.info(
                f"  Score: {match_result.get('total_score')} "
                f"({match_result.get('match_tier')})  |  "
                f"tokens in={usage.get('input_tokens')} out={usage.get('output_tokens')}"
            )

            tier = match_result.get("match_tier", "weak")
            tiers[tier] = tiers.get(tier, 0) + 1
            if match_result.get("trigger_cv_customisation"):
                cv_trigger += 1

            self.update_sheet(job_id, match_result)
            time.sleep(3)

        mode_label = "Profile-based" if self.use_profile else "Full CV text (fallback)"
        logger.info(
            f"Matching complete:\n"
            f"  Mode: {mode_label}\n"
            f"  Strong (85+):   {tiers['strong']} jobs\n"
            f"  Good   (70-84): {tiers['good']} jobs\n"
            f"  Ok     (60-69): {tiers['ok']} jobs\n"
            f"  Weak   (<60):   {tiers['weak']} jobs\n"
            f"  CV customisation triggered for: {cv_trigger} jobs"
        )
