"""
Email smoke test.

Sends a realistic test email to GMAIL_RECIPIENT using dummy pipeline data.
Does not touch the sheet or run any agents.

Usage:
    python3 test_email.py
"""

import sys
from loguru import logger
from config.settings import GMAIL_RECIPIENT, GOOGLE_SHEETS_ID, GOOGLE_DOCS_ID
from utils.gmail_client import GmailClient

logger.remove()
logger.add(sys.stdout, level="INFO", format="{message}")

_SHEETS_URL = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEETS_ID}/edit"
_DOCS_URL   = f"https://docs.google.com/document/d/{GOOGLE_DOCS_ID}/edit"

_DUMMY_STRONG = {
    "role": "Senior Product Manager — Growth",
    "company": "Razorpay",
    "match_score": "87",
    "url": "https://razorpay.com/jobs/jobs-all/detail/?gh_jid=0000001",
    "strong_areas": '["Strong growth experimentation background", "Platform scaling experience"]',
    "doc_tab_url": f"{_DOCS_URL}?tab=t.razorpay_test",
    "job_id": "test-job-id-001",
}

_DUMMY_GOOD = {
    "role": "Group Product Manager — Consumer",
    "company": "Meesho",
    "match_score": "76",
    "url": "https://meesho.io/jobs/detail/gpm-consumer",
    "doc_tab_url": f"{_DOCS_URL}?tab=t.meesho_test",
    "job_id": "test-job-id-002",
}

_DUMMY_WEAK = {
    "role": "Associate Product Manager — Lending",
    "company": "Paytm",
    "match_score": "31",
    "url": "https://jobs.lever.co/paytm/test-apm",
    "status": "weak_match",
    "job_id": "test-job-id-003",
}

SEP = "━" * 37


def build_test_body() -> str:
    from datetime import date
    import json

    date_str = str(date.today())
    strong   = _DUMMY_STRONG
    good_job = _DUMMY_GOOD
    weak_job = _DUMMY_WEAK

    try:
        strong_list = json.loads(strong["strong_areas"])
        strong_str  = "; ".join(strong_list[:2])
    except Exception:
        strong_str = "—"

    lines = [
        "Hi Debesh,",
        "",
        f"Here's your job hunt update for {date_str}.",
        f"(This is a TEST EMAIL — dummy data, no real scraping ran.)",
        "",
        SEP,
        "PIPELINE SUMMARY",
        SEP,
        "New jobs scraped:      3",
        "Extracted:             3",
        "Matched:               3",
        "CV customised:         2",
        "",
        SEP,
        "APPLY TODAY — STRONG MATCHES (85+)",
        SEP,
        (
            f"- {strong['role']} at {strong['company']} — Score: {strong['match_score']}\n"
            f"  URL:  {strong['url']}\n"
            f"  Strong: {strong_str}\n"
            f"  CV rec: {strong['doc_tab_url']}"
        ),
        "",
        SEP,
        "GOOD MATCHES — APPLY THIS WEEK (70-84)",
        SEP,
        (
            f"- {good_job['role']} at {good_job['company']} — Score: {good_job['match_score']}\n"
            f"  URL:  {good_job['url']}\n"
            f"  CV rec: {good_job['doc_tab_url']}"
        ),
        "",
        SEP,
        "OK MATCHES — REVIEW (60-69)",
        SEP,
        "None today.",
        "",
        SEP,
        "WEAK MATCHES — MANUAL REVIEW NEEDED",
        SEP,
        (
            f"- {weak_job['role']} at {weak_job['company']} — Score: {weak_job['match_score']}\n"
            f"  URL:  {weak_job['url']}\n"
            f"  Note: Skipped CV customisation.\n"
            f"  To override: python3 -c 'from agents.matching_agent import "
            f"MatchingAgent; MatchingAgent().trigger_manual_customisation(\"{weak_job['job_id']}\")'"
        ),
        "",
        SEP,
        f"Full job tracker: {_SHEETS_URL}",
        f"CV recommendations doc: {_DOCS_URL}",
        SEP,
        "",
        "Good luck today!",
        "Your Job Hunt Agent (TEST)",
    ]
    return "\n".join(lines)


def main():
    print(f"\nSending test email to: {GMAIL_RECIPIENT}")
    print("Building email body with dummy data...")

    subject = f"[TEST] Job Hunt Agent — email delivery check"
    body    = build_test_body()

    print(f"\nEmail preview (first 300 chars):")
    print(body[:300])
    print("...\n")

    gmail = GmailClient()
    gmail.send_email(subject, body)

    print(f"\nDone. Check {GMAIL_RECIPIENT} for the test email.")


if __name__ == "__main__":
    main()
