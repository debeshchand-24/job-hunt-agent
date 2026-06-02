# Job Hunt Automation Agent

A 5-agent autonomous pipeline that scrapes PM job postings daily across 15 companies, scores them against a candidate profile, customises CVs for top matches, and delivers a daily email digest.

**Status:** Production. Running daily via cron.

---

## The problem

Monitoring 15+ company career pages manually, filtering for relevant PM roles, and customising a CV per application was taking 2-3 hours per week. Most of that time was low-value — scanning irrelevant postings, copy-pasting JD keywords into a CV. I wanted a system that does the scanning automatically and surfaces only decisions that need a human.

---

## Architecture

A1 Scraper → A2 Extractor → A3 Matcher → A4 CV Customiser → A5 Admin

| Agent | Model | Job |
|-------|-------|-----|
| A1 Scraper | No LLM | Scrapes 15 company career pages, filters by role + location + dedup |
| A2 Extractor | Claude Haiku | Extracts structured JSON from raw JD text |
| A3 Matcher | Claude Sonnet | Scores job against candidate profile across 7 dimensions |
| A4 CV Customiser | Claude Sonnet | Rewrites CV sections for top 3 matches only |
| A5 Admin | No LLM | Orchestrates pipeline, sends daily email digest |

**Data layer:** Google Sheets as state machine. Status column drives agent gating: new → extracted → matched → cv_ready.

---

## Scoring rubric (A3)

100-point system across 7 dimensions:

| Dimension | Points | Notes |
|-----------|--------|-------|
| Skills match | 30 | Exact match full credit, adjacent 0.5x |
| Experience | 20 | Inverted-U curve — overqualification penalised same as underqualification |
| Role type fit | 15 | Exact / adjacent / transferable / stretch |
| Domain relevance | 15 | Same / adjacent / similar behaviour / different |
| Seniority alignment | 10 | Calibrated per company type (startup vs big tech) |
| Leadership scope | 5 | Only penalised if JD mentions it and CV does not show it |
| Others (conditional) | 10 | Only activates if JD explicitly mentions MBA/degree requirement |

**Tier thresholds:** 85+ strong, 70-84 good, 60-69 ok, below 60 weak, irrelevant auto-rejected.

---

## Key design decisions

**1. Pre-computed candidate profile over raw CV**
A3 uses a structured JSON profile (800 tokens) instead of raw CV text (2,500 tokens). Reduces cost by ~70% per match call with no quality loss.

**2. Two-layer irrelevant job filtering**
Layer 1: Title keyword filter at scrape time — zero token cost.
Layer 2: Core dimension minimum check at match time — if skills + domain + role_type all fall below threshold simultaneously, job is auto-rejected.

**3. Top 3 CV customisation only**
A4 runs only on jobs scoring 60+, capped at 3 per day. Most expensive operation — this keeps daily costs predictable.

**4. Character count constraint on rewrites**
Every A4 rewrite must stay within original length + 10 characters. Prevents layout breaks in the actual CV document.

**5. Config-driven scraper**
All company-specific scraping logic lives in config/companies.json, not code. Adding a new company = one JSON entry.

**6. Python enforces tier thresholds, not Claude**
Claude scores the 7 dimensions. Python applies tier thresholds and CV trigger rules deterministically. Separates probabilistic judgement from deterministic business logic.

---

## Eval suite

Built a 24-test eval suite across 3 agents after observing real pipeline failures.

Run: python3 run_evals.py

Results:
- A1 Scraper: 8/8 — 100%
- A3 Matching: 11/12 — 91%
- A4 CV Customiser: 2/4 — 50%
- Overall: 21/24 — 87%

3 known failures logged with fix notes. Eval suite distinguishes new failures from known ones and exits with error code on regressions.

Eval types: deterministic (exact assert), heuristic (range assert), LLM-as-judge (quality assessment).

---

## What I would do differently

Build the eval suite before the pipeline, not after. The scoring rubric produced surprising results on real data — adjacent domain scoring, overqualification curves — that I only discovered when running live. A gold-standard test set upfront would have caught rubric design issues earlier.

---

## Active companies (15)

Paytm, Groww, Meesho, Atlassian, Microsoft, Amazon, Uber, Swiggy, Razorpay, PhonePe, Flipkart, Adobe, Acko, UrbanCompany, Zepto

Phase 2 pending: Google Jobs and LinkedIn via Apify

---

## Stack

- Language: Python 3.9
- AI: Anthropic API — Haiku for extraction, Sonnet for matching and customisation
- Scraping: Playwright for JS-heavy sites, BeautifulSoup for static sites
- Data: Google Sheets via gspread, Google Docs
- Auth: Google OAuth 2.0
- Scheduling: cron 0 9 * * *
- CV output: Google Docs tabs, one per job
