# eval_suite/evals/eval_a1.py
# Tests A1 scraper filter functions.
# Zero API calls — pure Python, instant feedback.

import sys
sys.path.append('.')

from agents.scraping_agent import ScrapingAgent
from eval_suite.test_data.synthetic_jobs import SCRAPER_CASES

agent = ScrapingAgent()

# company dict that is_relevant_job expects
# content doesn't matter for title filter tests
DUMMY_COMPANY = {"name": "TestCo", "keywords": []}

results = []

def run_test(test_id, title, location, expected, reason, test_type):
    print(f"\nRunning A1-T{test_id}: {reason}")

    if test_type == "title":
        actual = agent.is_relevant_job(title, DUMMY_COMPANY)
    else:
        actual = agent.is_relevant_location(location)

    passed = actual == expected
    detail = (
        f"expected={expected} got={actual}"
    )
    results.append({
        "id": f"A1-T{test_id}",
        "description": reason,
        "eval_type": "deterministic",
        "passed": passed,
        "detail": detail
    })
    status = "✓ PASS" if passed else "✗ FAIL"
    print(f"  {status} — {detail}")

# ── Title filter tests ─────────────────────────────────

run_test(1,
    title="Programme Manager, Payments",
    location="Bangalore",
    expected=False,
    reason="Exclusion keyword: programme manager",
    test_type="title"
)

run_test(2,
    title="Senior Software Engineer",
    location="Bangalore",
    expected=False,
    reason="Exclusion keyword: software engineer",
    test_type="title"
)

run_test(3,
    title="Senior Product Manager — Growth",
    location="Bangalore",
    expected=True,
    reason="Primary keyword match, no exclusion",
    test_type="title"
)

run_test(4,
    title="Group Product Manager",
    location="Bangalore",
    expected=True,
    reason="Exact primary keyword match",
    test_type="title"
)

run_test(5,
    title="Product Manager - Hardware Platform",
    location="Bangalore",
    expected=True,
    reason="PM keyword present, hardware filtered downstream at A3",
    test_type="title"
)

# ── Location filter tests ──────────────────────────────

run_test(6,
    title="Senior Product Manager",
    location="Mumbai, Maharashtra",
    expected=False,
    reason="Location filter: Mumbai not accepted",
    test_type="location"
)

run_test(7,
    title="Senior Product Manager",
    location="Remote / Work from Home",
    expected=True,
    reason="Remote accepted in location filter",
    test_type="location"
)

run_test(8,
    title="Senior Product Manager",
    location="Bengaluru, Karnataka",
    expected=True,
    reason="Bengaluru variant accepted",
    test_type="location"
)

# ── Report ─────────────────────────────────────────────

print("\n" + "="*45)
print("EVAL REPORT — A1 Scraping Agent")
print("="*45)
passed = sum(1 for r in results if r["passed"])
total = len(results)
print(f"Score: {passed}/{total} passed\n")

for r in results:
    icon = "✓" if r["passed"] else "✗"
    print(f"{icon} {r['id']} {r['description']}")
    if not r["passed"]:
        print(f"     → {r['detail']}")

print("="*45)

# Return results for run_evals.py to consume
def get_results():
    return results
