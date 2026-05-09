import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID")
GOOGLE_DOCS_ID = os.getenv("GOOGLE_DOCS_ID")
GMAIL_RECIPIENT = os.getenv("GMAIL_RECIPIENT")

_required = {
    "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
    "GOOGLE_SHEETS_ID": GOOGLE_SHEETS_ID,
    "GOOGLE_DOCS_ID": GOOGLE_DOCS_ID,
    "GMAIL_RECIPIENT": GMAIL_RECIPIENT,
}

def validate():
    missing = [k for k, v in _required.items() if not v or v.endswith("_here")]
    if missing:
        raise EnvironmentError(f"Missing or unconfigured env variables: {', '.join(missing)}")

def load_companies():
    companies_path = Path(__file__).parent / "companies.json"
    with open(companies_path) as f:
        data = json.load(f)
    enabled = [c for c in data["companies"] if c.get("enabled")]
    logging.info(f"Loaded {len(enabled)} enabled companies (of {len(data['companies'])} total)")
    return enabled
