from loguru import logger
from config import settings


def main():
    logger.info("Starting job hunt agent pipeline...")

    try:
        settings.validate()
    except EnvironmentError as e:
        logger.error(f"Configuration error: {e}")
        return

    # Agent pipeline will be wired here:
    # 1. ScrapingAgent  — fetch raw job listings
    # 2. ExtractorAgent — parse listings into structured data
    # 3. MatchingAgent  — score and filter listings
    # 4. CVCustomiserAgent — tailor CV per top match
    # 5. AdminAgent     — log to Sheets, send Gmail summary

    logger.info("Pipeline complete.")


if __name__ == "__main__":
    main()
