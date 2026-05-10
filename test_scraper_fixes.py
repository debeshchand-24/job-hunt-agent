"""
Test the updated scraping logic on four JS-rendered companies.

For each company:
- Runs the scraper with the new filter_clicks, job_selectors, and pagination config
- Reports filter application, job count, first 3 titles, pages scraped, PASS/FAIL
- For Google: shows raw API response keys and first 3 titles

Usage:
    python3 test_scraper_fixes.py
"""

import sys
from loguru import logger

logger.remove()
logger.add(sys.stdout, level="INFO", format="{message}")

COMPANIES_TO_TEST = ["PhonePe", "Meesho", "Atlassian", "Google"]


def get_company(agent, name: str) -> dict:
    return next((c for c in agent.companies if c["name"] == name), None)


def run_single(agent, name: str) -> dict:
    company = get_company(agent, name)
    if company is None:
        print(f"  ERROR: {name} not found in config")
        return {"company": name, "jobs": [], "passed": False}

    pagination_type = company.get("pagination_type", "none")
    n_filters       = len(company.get("filter_clicks", []))
    scraper_type    = company.get("scraper_type", "playwright")

    print(f"\n{'─'*60}")
    print(f"  Testing: {name}")
    print(f"  Scraper: {scraper_type}  |  Pagination: {pagination_type}  |  Filter clicks: {n_filters}")
    print(f"{'─'*60}")

    # Google uses the API path — call it directly so we can inspect the raw response
    if name == "Google":
        print("  Using Google Careers JSON API (no Playwright)...")

        import requests
        _HEADERS = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": "https://careers.google.com/",
            "Accept": "application/json",
        }
        api_url = (
            "https://careers.google.com/api/jobs/jobs-site/search/"
            "?q=Product+Manager&location=Bangalore%2C+India&page=1"
        )
        try:
            resp = requests.get(api_url, headers=_HEADERS, timeout=15)
            print(f"  API status code: {resp.status_code}")

            if resp.status_code == 200:
                data = resp.json()
                print(f"  Top-level keys in response: {list(data.keys())}")

                raw_jobs = (
                    data.get("jobs")
                    or data.get("results")
                    or data.get("data")
                    or []
                )
                print(f"  Jobs array length: {len(raw_jobs)}")

                if raw_jobs:
                    print(f"  First job keys: {list(raw_jobs[0].keys())}")
                    print(f"  First 3 job titles:")
                    for j in raw_jobs[:3]:
                        title = j.get("title") or j.get("job_title") or j.get("name") or "?"
                        print(f"    • {title}")
                    jobs = [{"role": j.get("title", ""), "url": ""} for j in raw_jobs]
                else:
                    print("  No jobs found in response.")
                    jobs = []
            else:
                print(f"  API returned {resp.status_code} — check for bot blocking")
                jobs = []

        except Exception as exc:
            print(f"  API error: {exc}")
            jobs = []

    else:
        try:
            jobs = agent.scrape_javascript(company)
        except Exception as exc:
            logger.error(f"  {name}: scraper raised — {exc}")
            jobs = []

        filters_applied = n_filters > 0
        print(f"  Filter clicks configured: {'YES' if filters_applied else 'NO'}")
        print(f"  Jobs found: {len(jobs)}")

        if jobs:
            print(f"  First 3 job titles:")
            for j in jobs[:3]:
                print(f"    • {j.get('role', '—')[:70]}")
        else:
            print(f"  (no jobs extracted)")

        if pagination_type == "url" and jobs:
            from math import ceil
            est_pages = max(1, ceil(len(jobs) / 10))
            print(f"  Pages scraped (est.): ~{est_pages}")

    status = "PASS" if jobs else "FAIL"
    icon   = "✓" if jobs else "✗"
    print(f"  Status: {icon} {status} ({len(jobs)} jobs)")

    return {"company": name, "jobs": jobs, "passed": bool(jobs)}


def main():
    from agents.scraping_agent import ScrapingAgent

    print(f"\n{'═'*60}")
    print(f"  Scraper Fix Verification — {len(COMPANIES_TO_TEST)} companies")
    print(f"{'═'*60}")

    agent = ScrapingAgent()

    results = []
    for name in COMPANIES_TO_TEST:
        result = run_single(agent, name)
        results.append(result)

    passed = sum(1 for r in results if r.get("passed"))
    print(f"\n{'═'*60}")
    print(f"  Results: {passed}/{len(COMPANIES_TO_TEST)} companies working")
    print(f"{'═'*60}")
    for r in results:
        icon = "✓" if r.get("passed") else "✗"
        n    = len(r.get("jobs", []))
        print(f"  {icon} {r['company']:<15} {n} jobs")

    if passed < len(COMPANIES_TO_TEST):
        print("\n  Failed companies may need selector updates.")
        print("  Check logs above for specific errors.")


if __name__ == "__main__":
    main()
