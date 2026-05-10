import argparse
import sys

from loguru import logger
from config import settings


def main():
    parser = argparse.ArgumentParser(
        description="Job Hunt Agent — orchestrates scraping, extraction, matching and CV customisation."
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Run 60-day backfill on first run (skips duplicate check)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run pipeline once and exit (default behaviour)",
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Run on a daily 9 AM schedule (blocks forever)",
    )
    parser.add_argument(
        "--email-test",
        action="store_true",
        help="Send a test email using current sheet data and exit",
    )
    args = parser.parse_args()

    try:
        settings.validate()
    except EnvironmentError as exc:
        logger.error(f"Configuration error: {exc}")
        sys.exit(1)

    # Import here so validation errors surface before heavy imports
    from agents.admin_agent import AdminAgent

    agent = AdminAgent()

    if args.email_test:
        agent.send_daily_email()
    elif args.schedule:
        agent.schedule_daily()
    else:
        # Both --backfill and --once (or no flag) go through run_pipeline
        agent.run_pipeline(backfill=args.backfill)


if __name__ == "__main__":
    main()
