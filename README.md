# Job Hunt Agent

A multi-agent automation system that discovers job listings, matches them against your profile, customises your CV, and delivers a daily briefing to your inbox.

## What it does

```
Job Boards
    │
    ▼
ScrapingAgent       — Crawls job boards (LinkedIn, Indeed, etc.) using Playwright
    │
    ▼
ExtractorAgent      — Parses raw HTML into structured fields (title, company, salary, skills)
    │
    ▼
MatchingAgent       — Scores each listing against your CV and preferences; filters top matches
    │
    ▼
CVCustomiserAgent   — Rewrites your CV for each top-matched role using Claude
    │
    ▼
AdminAgent          — Logs results to Google Sheets, uploads CVs to Google Docs, emails a summary
```

Each agent is powered by Claude via the Anthropic API.

## Project structure

```
job-hunt-agent/
├── agents/             # One file per agent
├── config/             # settings.py loads all env variables
├── utils/              # Google Sheets, Docs, and Gmail API clients
├── data/my_cv.txt      # Your base CV (fill this in)
├── logs/               # Runtime logs (loguru)
├── .env                # API keys and IDs (never commit this)
├── requirements.txt
└── main.py             # Pipeline entry point
```

## Setup

**1. Install dependencies**
```bash
pip install -r requirements.txt
playwright install chromium
```

**2. Fill in `.env`**
```
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_SHEETS_ID=...        # The ID from your Google Sheet URL
GOOGLE_DOCS_ID=...          # The ID from your Google Doc URL
GMAIL_RECIPIENT=you@email.com
```

**3. Add your CV**

Paste your CV content into `data/my_cv.txt`.

**4. Set up Google credentials**

- Create a Google Cloud project and enable the Sheets, Docs, and Gmail APIs.
- Download `credentials.json` and place it in the project root.
- On first run, OAuth will open a browser window to authorise access.

**5. Run**
```bash
python main.py
```

## Agents

| Agent | Responsibility |
|---|---|
| `ScrapingAgent` | Browses job boards and collects raw listings |
| `ExtractorAgent` | Extracts structured data from raw HTML/text |
| `MatchingAgent` | Scores listings against your CV and preferences |
| `CVCustomiserAgent` | Tailors your CV for each target role via Claude |
| `AdminAgent` | Writes to Google Sheets, uploads Docs, sends Gmail digest |
