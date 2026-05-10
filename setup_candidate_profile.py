#!/usr/bin/env python3
"""
setup_candidate_profile.py

Run once to generate a structured candidate profile from the CV.
The profile is used by the matching agent instead of the full CV text,
cutting input tokens by ~75% per match call.

Usage:
    python3 setup_candidate_profile.py
"""

import json
import sys
from pathlib import Path

import anthropic
from loguru import logger

from config.settings import ANTHROPIC_API_KEY

ROOT    = Path(__file__).resolve().parent
DATA    = ROOT / "data"
PROFILE = DATA / "candidate_profile.json"

MODEL = "claude-sonnet-4-6"

_SYSTEM = (
    "You are an expert talent analyst. Extract a precise structured profile "
    "from this product manager CV. Be accurate and specific — this profile will "
    "be used for automated job matching. Return ONLY valid JSON, no markdown, no explanation."
)

# Double-braced {{ }} become literal { } after .format(); {cv_text} is substituted.
_USER_TEMPLATE = """\
Analyse this CV and extract a comprehensive candidate profile for job matching purposes.

CV TEXT:
{cv_text}

Return ONLY this exact JSON structure, no markdown, no explanation:
{{
  "name": "<full name from CV>",
  "current_title": "<most recent job title>",
  "total_experience_years": <number>,
  "product_experience_years": <number>,

  "skills": {{
    "core_pm_skills":   ["<top PM skills, max 15>"],
    "technical_skills": ["<tools, platforms, tech>"],
    "domain_skills":    ["<domain specific knowledge>"],
    "soft_skills":      ["<leadership, communication etc>"]
  }},

  "domains": [
    {{
      "name":  "<industry/domain>",
      "depth": "<deep/moderate/surface>",
      "years": "<approximate years>"
    }}
  ],

  "role_types_experience": ["<growth, consumer, platform, b2b etc>"],

  "seniority_profile": {{
    "current_level":      "<APM/PM/SPM/Lead/Director/VP>",
    "team_size_managed":  "<number or range>",
    "budget_owned":       "<if mentioned, else null>",
    "leadership_evidence": ["<specific evidence from CV>"]
  }},

  "key_achievements": [
    {{
      "description": "<achievement>",
      "metric":      "<quantified impact if available>",
      "domain":      "<which domain/company>"
    }}
  ],

  "education": {{
    "degree":      "<highest degree>",
    "field":       "<field of study>",
    "institution": "<college/university>",
    "tier":        "<tier1/tier2/other>",
    "has_mba":     <true/false>,
    "has_masters": <true/false>
  }},

  "companies_worked": [
    {{
      "name":   "<company>",
      "type":   "<startup/bigtech/mid-size>",
      "domain": "<industry>",
      "role":   "<title>",
      "years":  "<approximate>"
    }}
  ],

  "career_trajectory": "<1 sentence summary of career arc and direction>",

  "strongest_for_roles": ["<3-5 role types this candidate is strongest for>"],

  "target_seniority": {{
    "startups_consumer": [
      "Group PM", "Principal PM", "Associate Director Product", "Director Product"
    ],
    "big_tech": ["SPM", "Lead PM", "Senior Product Manager"]
  }},

  "red_flags_for_jd_matching": [
    "<things that would be mismatches, e.g. very early stage only, no B2B experience>"
  ]
}}"""


# ── Step 1: Read CV ───────────────────────────────────────────────────────

def load_cv() -> tuple[str, str]:
    """Return (cv_text, source_filename). Raises if no CV found."""
    candidates = [
        DATA / "growth_cv_text.txt",
        DATA / "growth_cv.txt",
    ]
    for path in candidates:
        if path.exists() and path.stat().st_size > 0:
            text = path.read_text(encoding="utf-8")
            logger.info(f"Reading CV from: {path.name} ({len(text):,} chars)")
            return text, path.name
    raise FileNotFoundError(
        "CV file not found. Run setup_cvs.py first.\n"
        f"Looked for: {[str(p) for p in candidates]}"
    )


# ── Step 2: Extract profile via Claude ───────────────────────────────────

def extract_profile(cv_text: str) -> tuple[dict, dict]:
    """Call Claude and return (parsed_profile, usage_dict)."""
    client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt  = _USER_TEMPLATE.format(cv_text=cv_text)

    response = client.messages.create(
        model      = MODEL,
        max_tokens = 4096,
        system     = _SYSTEM,
        messages   = [{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    usage = {
        "input_tokens":  response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
    }

    # Strip markdown fences if present
    clean = raw
    if clean.startswith("```"):
        clean = clean.split("\n", 1)[-1]
        clean = clean.rsplit("```", 1)[0].strip()

    try:
        profile = json.loads(clean)
    except json.JSONDecodeError as exc:
        logger.error(f"JSON parse failed: {exc}")
        logger.error(f"Raw response (first 500 chars): {raw[:500]}")
        sys.exit(1)

    return profile, usage


# ── Step 3: Validate and save ─────────────────────────────────────────────

REQUIRED_KEYS = {"name", "skills", "domains", "total_experience_years"}

def validate_and_save(profile: dict) -> int:
    missing = REQUIRED_KEYS - set(profile.keys())
    if missing:
        logger.warning(f"Profile missing keys: {missing} — saving anyway")

    PROFILE.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    size = PROFILE.stat().st_size
    logger.info(f"Profile saved to {PROFILE} ({size:,} bytes)")
    return size


# ── Step 4: Print summary ─────────────────────────────────────────────────

def print_summary(profile: dict, cv_chars: int, profile_chars: int, usage: dict):
    name         = profile.get("name", "?")
    title        = profile.get("current_title", "?")
    total_exp    = profile.get("total_experience_years", "?")
    prod_exp     = profile.get("product_experience_years", "?")
    skills_data  = profile.get("skills", {})
    core_skills  = skills_data.get("core_pm_skills", [])[:5]
    domains      = [d.get("name", "") for d in profile.get("domains", [])]
    strongest    = profile.get("strongest_for_roles", [])
    saving_pct   = round((cv_chars - profile_chars) / cv_chars * 100) if cv_chars else 0

    border = "═" * 40
    print(f"""
{border}
CANDIDATE PROFILE GENERATED
{border}
Name           : {name}
Current title  : {title}
Experience     : {total_exp} years total, {prod_exp} years product

Core skills    : {", ".join(core_skills)}
Domains        : {", ".join(domains)}
Strongest for  : {", ".join(strongest)}

Profile saved to : data/candidate_profile.json
Size             : {profile_chars:,} chars
vs CV            : {cv_chars:,} chars
Token saving/match: ~{saving_pct}%
{border}

Token usage:
  Input  : {usage['input_tokens']:,}
  Output : {usage['output_tokens']:,}
""")


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{message}")

    # Step 1
    cv_text, cv_source = load_cv()
    cv_chars = len(cv_text)

    # Step 2
    print(f"Extracting profile from {cv_source} using {MODEL} ...")
    profile, usage = extract_profile(cv_text)

    # Step 3
    validate_and_save(profile)
    profile_chars = len(json.dumps(profile))

    # Step 4
    print_summary(profile, cv_chars, profile_chars, usage)


if __name__ == "__main__":
    main()
