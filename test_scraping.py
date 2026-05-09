"""
Quick smoke test for the ScrapingAgent.

Tests two static companies with known ATS structures (no Playwright needed):
  - Paytm  → Lever ATS
  - Groww  → Greenhouse ATS

Covers the full pipeline: listing scrape → keyword filter → JD fetch.

Usage:
    python3 test_scraping.py          # dry run — prints results only
    python3 test_scraping.py --save   # also saves to Google Sheets
"""

import sys
from loguru import logger

import requests
from agents.scraping_agent import _fetch_jd_static, _parse_html

# Suppress loguru's default handler so we control all output ourselves.
logger.remove()
logger.add(sys.stderr, level="WARNING")

_PM_KEYWORDS = [
    "product manager", "pm", "product management", "apm",
    "associate product manager", "senior product manager",
    "group product manager", "vp product", "director of product",
    "head of product", "product lead",
]

TEST_COMPANIES = [
    {
        # Lever ATS — clean static HTML, no JS rendering needed.
        "name": "Paytm",
        "careers_url": "https://jobs.lever.co/paytm?location=Bangalore%2C%20Karnataka&department=Product",
        "type": "static",
        "enabled": True,
        "url_filtered": True,
        "notes": "Lever ATS - consistent structure",
        "filter_keywords": _PM_KEYWORDS,
    },
    {
        "name": "Groww",
        "careers_url": "https://job-boards.eu.greenhouse.io/groww?offices%5B%5D=4018978101",
        "type": "static",
        "enabled": True,
        "url_filtered": False,
        "notes": "Greenhouse ATS - consistent static structure",
        "filter_keywords": _PM_KEYWORDS,
    },
]

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def scrape_company(company: dict) -> list[dict]:
    resp = requests.get(company["careers_url"], headers=_BROWSER_HEADERS, timeout=30)
    resp.raise_for_status()
    return _parse_html(company, resp.text)


def filter_pm_roles(jobs: list[dict], company: dict) -> list[dict]:
    keywords = company.get("filter_keywords", [])
    return [j for j in jobs if any(kw in j.get("role", "").lower() for kw in keywords)]


def print_results(company_name: str, all_jobs: list[dict], pm_jobs: list[dict], jd_preview: str):
    print(f"\n{'='*60}")
    print(f"  {company_name}")
    print(f"{'='*60}")
    print(f"  Raw listings : {len(all_jobs)}")
    print(f"  PM roles     : {len(pm_jobs)}")

    if not pm_jobs:
        print("  (no PM roles found — none to fetch JD for)")
        return

    first = pm_jobs[0]
    print(f"\n  First PM role:")
    print(f"    Title    : {first.get('role', '—')}")
    print(f"    URL      : {first.get('url', '—')}")
    print(f"    Location : {first.get('location') or '(not extracted)'}")

    print(f"\n  JD preview (first 500 chars):")
    if jd_preview:
        print(f"    {jd_preview[:500]}")
    else:
        print("    (JD fetch returned empty — check URL or site structure)")


def main(save: bool = False):
    print("\nScraping Agent — full pipeline smoke test")
    print(f"Companies: {', '.join(c['name'] for c in TEST_COMPANIES)} (static only — no Playwright)")
    print(f"Save to Sheets: {save}\n")

    results: dict[str, dict] = {}

    for company in TEST_COMPANIES:
        name = company["name"]

        # Step 1: Scrape listing page
        print(f"[{name}] Scraping listing page ...", end=" ", flush=True)
        try:
            all_jobs = scrape_company(company)
            print(f"{len(all_jobs)} listings")
        except Exception as exc:
            print(f"FAILED — {exc}")
            results[name] = {"all_jobs": [], "pm_jobs": [], "jd_preview": ""}
            continue

        # Step 2: Filter PM roles
        pm_jobs = filter_pm_roles(all_jobs, company)
        skipped = len(all_jobs) - len(pm_jobs)
        print(f"[{name}] Keyword filter: {len(pm_jobs)} PM roles kept, {skipped} skipped")

        # Step 3: Fetch JD for first PM job
        jd_preview = ""
        if pm_jobs:
            first_url = pm_jobs[0].get("url", "")
            print(f"[{name}] Fetching JD from {first_url} ...", end=" ", flush=True)
            try:
                jd_text = _fetch_jd_static(first_url)
                jd_preview = jd_text
                print(f"{len(jd_text)} chars")
            except Exception as exc:
                print(f"FAILED — {exc}")

        results[name] = {"all_jobs": all_jobs, "pm_jobs": pm_jobs, "jd_preview": jd_preview}
        print_results(name, all_jobs, pm_jobs, jd_preview)

    # Summary
    total_pm = sum(len(v["pm_jobs"]) for v in results.values())
    jd_fetched = sum(1 for v in results.values() if v["jd_preview"])
    print(f"\n{'='*60}")
    print(f"  SUMMARY")
    print(f"{'='*60}")
    print(f"  Total PM roles found : {total_pm}")
    print(f"  JDs successfully fetched : {jd_fetched} / {sum(1 for v in results.values() if v['pm_jobs'])}")
    print(f"{'='*60}\n")

    if save:
        print("Saving to Google Sheets ...")
        from agents.scraping_agent import ScrapingAgent
        agent = ScrapingAgent()
        all_pm_jobs = [j for v in results.values() for j in v["pm_jobs"]]
        saved = agent.save_jobs(all_pm_jobs)
        print(f"Saved {saved} new jobs to sheet (JDs fetched inline by save_jobs).")
    else:
        print("Dry run complete. Pass --save to write results to Google Sheets.")


if __name__ == "__main__":
    save_flag = "--save" in sys.argv
    main(save=save_flag)
