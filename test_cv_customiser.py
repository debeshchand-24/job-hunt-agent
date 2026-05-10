"""
Dry-run test for CVCustomiserAgent.

Reads the first job with status='matched' (trigger_cv_customisation implied),
runs generate_suggestions(), and prints the full output to the terminal.
Does NOT write to Google Docs or update the sheet.

Usage:
    python3 test_cv_customiser.py
"""

import json
import sys

from loguru import logger

from agents.cv_customiser_agent import CVCustomiserAgent

logger.remove()
logger.add(sys.stdout, level="INFO", format="{message}")

SEP = "=" * 65


def main():
    print(f"\n{SEP}")
    print("  CVCustomiserAgent — dry-run test (no doc writes)")
    print(SEP)

    agent = CVCustomiserAgent()

    jobs = agent.get_jobs_to_customise()
    if not jobs:
        print("\nNo matched jobs found (status='matched' with empty doc_tab_url).")
        print("Run the MatchingAgent first: python3 test_matching.py --save")
        return

    job = jobs[0]
    role    = job.get("role", "?")
    company = job.get("company", "?")
    score   = job.get("match_score", "?")
    tier    = job.get("match_tier", "?")

    # Show CV selection choice
    _, cv_version = agent.select_cv(job)

    print(f"\nJob 1/{len(jobs)}: {role}")
    print(f"  Company    : {company}")
    print(f"  Score      : {score}/100  |  Tier: {tier}")
    print(f"  CV version : {cv_version}")
    print(f"  Context doc: {len(agent.context_doc):,} chars")
    print()

    # Show extracted JD summary
    try:
        extracted = json.loads(job.get("extracted_skills", "") or "{}")
        print(f"  Seniority  : {extracted.get('seniority_level', '?')}")
        print(f"  Domain     : {extracted.get('domain', '?')}")
        print(f"  Role type  : {extracted.get('role_type', '?')}")
        req_skills = extracted.get("required_skills", [])
        print(f"  Req skills : {', '.join(req_skills[:5])}{'...' if len(req_skills) > 5 else ''}")
    except Exception:
        pass

    print(f"\n{SEP}")
    print("  Calling Claude API for suggestions...")
    print(SEP)

    try:
        suggestions = agent.generate_suggestions(job)
    except Exception as exc:
        print(f"\nAPI ERROR: {exc}")
        return

    print(f"\n{SEP}")
    print("  SUGGESTIONS OUTPUT")
    print(SEP)
    print()
    print(suggestions)
    print()
    print(SEP)
    print(f"  Dry run complete — suggestions generated, no doc or sheet writes.")
    print(SEP)


if __name__ == "__main__":
    main()
