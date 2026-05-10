"""
Dry-run test for MatchingAgent.

Reads first 2 jobs with status='extracted' from Google Sheets,
runs match_job() on each, and pretty-prints the full result.
Does NOT write anything back to the sheet.

Usage:
    python3 test_matching.py
"""

import json
import sys

from loguru import logger

from agents.matching_agent import MatchingAgent

logger.remove()
logger.add(sys.stdout, level="INFO", format="{message}")

SEP  = "=" * 65
SEP2 = "-" * 65


def _score_bar(score: int, max_score: int, width: int = 20) -> str:
    filled = round(score / max_score * width)
    return f"[{'█' * filled}{'░' * (width - filled)}] {score}/{max_score}"


def _override_tag(final_val: bool, claude_val) -> str:
    """Show whether the value was Claude's choice or a threshold override."""
    if claude_val is None:
        return "(threshold rule)"
    return "(Claude agreed)" if final_val == claude_val else f"(overridden — Claude said {claude_val})"


def _profile_field_annotations(profile: dict) -> dict[str, str]:
    """Extract concise per-dimension values from the candidate profile for display."""
    s   = profile.get("skills", {})
    sp  = profile.get("seniority_profile", {})
    edu = profile.get("education", {})
    return {
        "skills_score":     f"{len(s.get('core_pm_skills', []))} core + {len(s.get('technical_skills', []))} tech skills",
        "experience_score": f"{profile.get('total_experience_years', '?')}yr total, {profile.get('product_experience_years', '?')}yr PM",
        "role_type_score":  ", ".join(profile.get("role_types_experience", [])[:3]),
        "domain_score":     ", ".join(d.get("name", "") for d in profile.get("domains", [])[:3]),
        "seniority_score":  sp.get("current_level", "?"),
        "leadership_score": f"{len(sp.get('leadership_evidence', []))} evidence points",
        "others_score":     f"MBA={'yes' if edu.get('has_mba') else 'no'}, {edu.get('tier', '?')} college",
    }


def print_match_result(job: dict, result: dict, cv_name: str, usage: dict,
                       claude_trigger=None, claude_apply=None,
                       match_mode: str = "unknown", profile=None):
    role    = job.get("role", "?")
    company = job.get("company", "?")

    total  = result.get("total_score", 0)
    tier   = result.get("match_tier", "?").upper()

    tier_icons = {"STRONG": "🟢", "GOOD": "🔵", "MODERATE": "🟡", "WEAK": "🔴"}
    icon = tier_icons.get(tier, "⚪")

    print(f"\n{SEP}")
    print(f"  {role}")
    print(f"  @ {company}")
    print(SEP)
    mode_labels = {
        "profile":  "Profile-based (structured JSON)",
        "cv_text":  "Full CV text (fallback)",
        "cv_pdf":   "PDF document (fallback)",
        "unknown":  "Unknown",
    }
    print(f"  {icon} {tier} MATCH   Total: {total}/100")
    print(f"  CV used: {cv_name}  |  Mode: {mode_labels.get(match_mode, match_mode)}")
    print()

    # Score breakdown — annotate with profile field values when in profile mode
    annotations = _profile_field_annotations(profile) if (match_mode == "profile" and profile) else {}
    print("  Score breakdown:")
    dims = [
        ("Skills",     "skills_score",      30),
        ("Experience", "experience_score",  20),
        ("Role type",  "role_type_score",   15),
        ("Domain",     "domain_score",      15),
        ("Seniority",  "seniority_score",   10),
        ("Leadership", "leadership_score",   5),
        ("Other reqs", "others_score",      10),
    ]
    for label, key, max_pts in dims:
        score      = result.get(key, 0)
        bar        = _score_bar(score, max_pts, width=15)
        annotation = annotations.get(key, "")
        suffix     = f"  ← {annotation}" if annotation else ""
        if key == "leadership_score":
            print(f"    {label:<12} {bar}{suffix}")
            reason = result.get("leadership_reasoning", "")
            if reason:
                print(f"               ↳ {reason}")
        else:
            print(f"    {label:<12} {bar}{suffix}")

    print()

    # Strong areas
    strong = result.get("strong_areas", [])
    if strong:
        print("  Strong areas:")
        for s in strong:
            print(f"    + {s}")

    # Weak areas
    weak = result.get("weak_areas", [])
    if weak:
        print("  Weak / gaps:")
        for w in weak:
            print(f"    - {w}")

    # Absent skills
    absent = result.get("absent_areas", [])
    if absent:
        print("  Absent skills:")
        for a in absent:
            print(f"    ✗ {a}")

    print()
    print(f"  Reasoning:")
    print(f"    {result.get('reasoning', '—')}")

    print()
    score    = result.get("total_score", 0)
    tier     = result.get("match_tier", "?")
    priority = result.get("priority", False)
    apply_val = result.get("apply_recommended")
    cv_val    = result.get("trigger_cv_customisation")
    apply_tag = _override_tag(apply_val, claude_apply)
    cv_tag    = _override_tag(cv_val, claude_trigger)

    manual_available = (tier == "weak")

    print(f"  Score: {score} | Tier: {tier.upper()} | Priority: {priority}")
    print(f"  CV Customisation: {'YES' if cv_val else 'no':<4} {cv_tag}")
    print(f"  Apply recommended: {'YES' if apply_val else 'no':<4} {apply_tag}")
    print(f"  Manual override available: {manual_available}")

    print()
    print(f"  Token usage — input: {usage.get('input_tokens', '?')}, "
          f"output: {usage.get('output_tokens', '?')}")


def main():
    print(f"\n{SEP}")
    print("  MatchingAgent — dry-run test (no sheet writes)")
    print(SEP)

    agent = MatchingAgent()

    # Show which matching mode is active
    mode_label = "Profile-based" if agent.use_profile else "Full CV text (fallback)"
    print(f"\n  Matching mode: {mode_label}")
    if agent.use_profile:
        p = agent.candidate_profile
        print(f"  Profile: {p.get('name')} | {p.get('total_experience_years')}yr total, "
              f"{p.get('product_experience_years')}yr PM | "
              f"{p.get('seniority_profile', {}).get('current_level', '?')}")

    jobs = agent.get_jobs_to_match()
    if not jobs:
        print("\nNo jobs with status='extracted' found in sheet.")
        print("Run the ExtractorAgent first:  python3 test_extractor.py  (with --save)")
        return

    sample = jobs[:2]
    print(f"\nJobs available: {len(jobs)}  |  Testing first {len(sample)}\n")

    for i, job in enumerate(sample, start=1):
        role    = job.get("role", "?")
        company = job.get("company", "?")
        print(f"  [{i}/{len(sample)}] Matching: {role} at {company} ...")

        # Parse extracted data to show CV selection reason
        try:
            extracted = json.loads(job.get("extracted_skills", "{}") or "{}")
        except json.JSONDecodeError:
            extracted = {}

        # Dry run — call match_job but do NOT call update_sheet
        result = agent.match_job(job)

        if not result:
            print(f"  ERROR: match_job returned empty result — check logs\n")
            continue

        if "_api_error" in result:
            print(f"  API ERROR: {result['_api_error']}\n")
            continue

        if "_parse_error" in result:
            print(f"  PARSE ERROR — Claude response was not valid JSON")
            print(f"  Raw response:\n{result.get('_raw_response', '')[:400]}\n")
            continue

        usage          = result.pop("_usage", {})
        claude_trigger = result.pop("_claude_trigger", None)
        claude_apply   = result.pop("_claude_apply", None)
        match_mode     = result.pop("_match_mode", "unknown")
        cv_name        = result.get("which_cv_used", "?")
        print_match_result(
            job, result, cv_name, usage,
            claude_trigger, claude_apply,
            match_mode=match_mode,
            profile=agent.candidate_profile if agent.use_profile else None,
        )

    print(f"\n{SEP}")
    print(f"  Dry run complete. {len(sample)} jobs matched, 0 sheet writes.")
    print(SEP)


if __name__ == "__main__":
    main()
