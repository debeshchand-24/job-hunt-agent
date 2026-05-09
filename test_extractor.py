"""
Dry-run test for ExtractorAgent.

Reads the first 2 unprocessed jobs from Google Sheets, runs Claude
extraction on each, and pretty-prints the result.
Does NOT write anything back to the sheet.

Usage:
    python3 test_extractor.py
"""

import json
import sys

from loguru import logger

from agents.extractor_agent import ExtractorAgent

logger.remove()
logger.add(sys.stdout, level="INFO", format="{message}")

SEP = "=" * 65


def main():
    print(f"\n{SEP}")
    print("  ExtractorAgent — dry-run test (no sheet writes)")
    print(SEP)

    agent = ExtractorAgent()

    jobs = agent.get_unprocessed_jobs()
    if not jobs:
        print("\nNo unprocessed jobs in sheet (status=new, jd_raw>200 chars, extracted_skills empty).")
        print("Run the scraping agent first, or set a row's status back to 'new' to re-test.")
        return

    sample = jobs[:2]
    print(f"\nJobs available: {len(jobs)}  |  Testing first {len(sample)}\n")

    for i, job in enumerate(sample, start=1):
        role    = job.get("role", "?")
        company = job.get("company", "?")
        jd_len  = len(job.get("jd_raw", ""))

        print(f"{SEP}")
        print(f"  Job {i}/{len(sample)}: {role}")
        print(f"  Company : {company}")
        print(f"  JD size : {jd_len} chars")
        print(SEP)

        print(f"\nCalling Claude ({agent.model}) ...")
        extracted = agent.extract_jd(job)

        usage = extracted.get("_usage", {})
        print(f"Tokens used — input: {usage.get('input_tokens', '?')}, "
              f"output: {usage.get('output_tokens', '?')}\n")

        # Strip internal metadata for display
        display = {k: v for k, v in extracted.items() if not k.startswith("_")}

        if "_parse_error" in extracted:
            print("WARNING: Claude response was not valid JSON.")
            print(f"Raw response:\n{extracted.get('_raw_response', '')}\n")
        else:
            print("Extracted JSON:")
            print(json.dumps(display, indent=2))

        print()

    print(SEP)
    print(f"  Dry run complete. {len(sample)} jobs extracted, 0 sheet writes.")
    print(SEP)


if __name__ == "__main__":
    main()
