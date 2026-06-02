import time
from datetime import date
from typing import Optional

from loguru import logger

from agents.cv_customiser_agent import CVCustomiserAgent
from agents.extractor_agent import ExtractorAgent
from agents.matching_agent import MatchingAgent
from agents.scraping_agent import ScrapingAgent
from config.settings import GMAIL_RECIPIENT, GOOGLE_DOCS_ID, GOOGLE_SHEETS_ID
from utils.gmail_client import GmailClient
from utils.sheets_client import SheetsClient

_SHEETS_URL = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEETS_ID}/edit"
_DOCS_URL   = f"https://docs.google.com/document/d/{GOOGLE_DOCS_ID}/edit"


def _fmt(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


class AdminAgent:
    def __init__(self):
        self.scraping   = ScrapingAgent()
        self.extractor  = ExtractorAgent()
        self.matcher    = MatchingAgent()
        self.customiser = CVCustomiserAgent()
        self.gmail      = GmailClient()
        self.sheets     = SheetsClient()
        self.run_date   = date.today()
        logger.info(f"Admin Agent initialised for {self.run_date}")

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _count_by_status(self, *statuses: str) -> int:
        rows = self.sheets.get_all_rows()
        return sum(1 for r in rows if r.get("status") in statuses)

    def _rows_by_status(self, *statuses: str) -> list[dict]:
        return [r for r in self.sheets.get_all_rows() if r.get("status") in statuses]

    def _count_eligible_for_cv(self) -> int:
        rows = self.sheets.get_all_rows()
        return sum(
            1 for r in rows
            if r.get("status") == "matched"
            and not r.get("doc_tab_url", "").strip()
        )

    # ------------------------------------------------------------------ #
    # Pipeline
    # ------------------------------------------------------------------ #

    def run_pipeline(self, backfill: bool = False):
        pipeline_start = time.monotonic()
        errors: list[str] = []
        pipeline_stats = {
            "new_scraped": 0,
            "extracted":   0,
            "matched":     0,
            "cv_customised": 0,
        }

        logger.info(f"━━ Pipeline starting for {self.run_date} (backfill={backfill}) ━━")

        # ── STEP 1: Scraping ─────────────────────────────────────────────
        logger.info("STEP 1/4: Starting Scraping Agent...")
        t0 = time.monotonic()
        try:
            new_jobs = self.scraping.run(backfill=backfill)
            pipeline_stats["new_scraped"] = new_jobs
            logger.info(f"Scraping complete in {_fmt(time.monotonic() - t0)}: {new_jobs} new jobs added")
            if new_jobs == 0 and not backfill:
                logger.info("No new jobs found today. Continuing to check for unprocessed jobs...")
        except Exception as exc:
            msg = f"Scraping agent failed: {exc}"
            logger.error(msg)
            errors.append(f"⚠️ {msg}")

        # ── STEP 2: Extraction ───────────────────────────────────────────
        logger.info("STEP 2/4: Starting Extractor Agent...")
        t0 = time.monotonic()
        unprocessed = self._count_by_status("new")
        if unprocessed == 0:
            logger.info("No jobs to extract. Skipping.")
        else:
            try:
                self.extractor.run()
                pipeline_stats["extracted"] = unprocessed
                logger.info(f"Extraction complete in {_fmt(time.monotonic() - t0)}")
            except Exception as exc:
                msg = f"Extractor agent failed: {exc}"
                logger.error(msg)
                errors.append(f"⚠️ {msg}")

        # ── STEP 3: Matching ─────────────────────────────────────────────
        logger.info("STEP 3/4: Starting Matching Agent...")
        t0 = time.monotonic()
        to_match = self._count_by_status("extracted")
        if to_match == 0:
            logger.info("No jobs to match. Skipping.")
        else:
            try:
                self.matcher.run()
                pipeline_stats["matched"] = to_match
                logger.info(f"Matching complete in {_fmt(time.monotonic() - t0)}")
            except Exception as exc:
                msg = f"Matching agent failed: {exc}"
                logger.error(msg)
                errors.append(f"⚠️ {msg}")

        # ── STEP 4: CV Customisation ─────────────────────────────────────
        logger.info("STEP 4/4: Starting CV Customiser...")
        t0 = time.monotonic()
        eligible = self._count_eligible_for_cv()
        if eligible == 0:
            logger.info("No jobs eligible for CV customisation.")
        else:
            try:
                self.customiser.run()
                pipeline_stats["cv_customised"] = eligible
                logger.info(f"CV customisation complete in {_fmt(time.monotonic() - t0)}")
            except Exception as exc:
                msg = f"CV Customiser agent failed: {exc}"
                logger.error(msg)
                errors.append(f"⚠️ {msg}")

        # ── STEP 5: Email summary ────────────────────────────────────────
        logger.info("Sending daily email summary...")
        try:
            self.send_daily_email(pipeline_stats=pipeline_stats, errors=errors)
        except Exception as exc:
            logger.error(f"Email failed: {exc}")

        total = time.monotonic() - pipeline_start
        logger.info(
            f"Pipeline complete for {self.run_date}. "
            f"Total pipeline time: {_fmt(total)}"
        )

    # ------------------------------------------------------------------ #
    # Email
    # ------------------------------------------------------------------ #

    def send_daily_email(
        self,
        pipeline_stats: Optional[dict] = None,
        errors: Optional[list] = None,
    ):
        if pipeline_stats is None:
            pipeline_stats = {}
        if errors is None:
            errors = []

        rows = self.sheets.get_all_rows()

        # Separate by tier
        strong = [r for r in rows if r.get("match_tier") == "strong"]
        good   = [r for r in rows if r.get("match_tier") == "good"]
        ok     = [r for r in rows if r.get("match_tier") == "ok"]
        weak   = [r for r in rows if r.get("status") == "weak_match"]
        matched_count    = len(strong) + len(good) + len(ok)
        irrelevant_count = sum(1 for r in rows if r.get("status") == "irrelevant")

        date_str = str(self.run_date)

        # Short email if nothing interesting
        all_matched = strong + good + ok + weak
        if not all_matched and pipeline_stats.get("new_scraped", 0) == 0:
            subject = f"Job Hunt Update — {date_str} | No new jobs today"
            body = (
                f"Hi Debesh,\n\n"
                f"No new PM jobs found today across your target companies.\n"
                f"Will check again tomorrow at 9AM.\n\n"
                f"Full tracker: {_SHEETS_URL}\n\n"
                f"Your Job Hunt Agent"
            )
            self.gmail.send_email(subject, body)
            logger.info(f"Email sent to {GMAIL_RECIPIENT}")
            return

        # Full email
        subject = (
            f"Job Hunt Update — {date_str} | "
            f"{pipeline_stats.get('new_scraped', 0)} new jobs | "
            f"{matched_count} to apply"
        )

        sep = "━" * 37

        def _job_block_full(r: dict) -> str:
            strong_raw = r.get("strong_areas", "[]")
            try:
                strong_list = __import__("json").loads(strong_raw)
                strong_str  = "; ".join(strong_list[:2]) if strong_list else "—"
            except Exception:
                strong_str = strong_raw[:80]
            doc = r.get("doc_tab_url", "") or "Not yet generated"
            return (
                f"- {r.get('role', '?')} at {r.get('company', '?')} "
                f"— Score: {r.get('match_score', '?')}\n"
                f"  URL:  {r.get('url', '—')}\n"
                f"  Strong: {strong_str}\n"
                f"  CV rec: {doc}"
            )

        def _job_block_brief(r: dict) -> str:
            doc = r.get("doc_tab_url", "") or "Not yet generated"
            return (
                f"- {r.get('role', '?')} at {r.get('company', '?')} "
                f"— Score: {r.get('match_score', '?')}\n"
                f"  URL:  {r.get('url', '—')}\n"
                f"  CV rec: {doc}"
            )

        def _job_block_weak(r: dict) -> str:
            jid = r.get("job_id", "")
            return (
                f"- {r.get('role', '?')} at {r.get('company', '?')} "
                f"— Score: {r.get('match_score', '?')}\n"
                f"  URL:  {r.get('url', '—')}\n"
                f"  Note: Skipped CV customisation.\n"
                f"  To override: python3 -c 'from agents.matching_agent import "
                f"MatchingAgent; MatchingAgent().trigger_manual_customisation(\"{jid}\")'"
            )

        lines = [
            f"Hi Debesh,",
            f"",
            f"Here's your job hunt update for {date_str}.",
            f"",
            sep,
            "PIPELINE SUMMARY",
            sep,
            f"New jobs scraped:      {pipeline_stats.get('new_scraped', 0)}",
            f"Extracted:             {pipeline_stats.get('extracted', 0)}",
            f"Matched:               {pipeline_stats.get('matched', 0)}",
            f"CV customised:         {pipeline_stats.get('cv_customised', 0)}",
            f"Auto-rejected:         {irrelevant_count} (wrong domain/role — not PM roles)",
        ]

        if errors:
            lines += ["", "ERRORS THIS RUN"] + errors

        # Strong
        lines += ["", sep, "APPLY TODAY — STRONG MATCHES (85+)", sep]
        if strong:
            lines += [_job_block_full(r) for r in strong]
        else:
            lines.append("None today.")

        # Good
        lines += ["", sep, "GOOD MATCHES — APPLY THIS WEEK (70-84)", sep]
        if good:
            lines += [_job_block_brief(r) for r in good]
        else:
            lines.append("None today.")

        # Ok
        lines += ["", sep, "OK MATCHES — REVIEW (60-69)", sep]
        if ok:
            lines += [_job_block_brief(r) for r in ok]
        else:
            lines.append("None today.")

        # Weak
        lines += ["", sep, "WEAK MATCHES — MANUAL REVIEW NEEDED", sep]
        if weak:
            lines += [_job_block_weak(r) for r in weak]
        else:
            lines.append("None today.")

        lines += [
            "",
            sep,
            f"Full job tracker: {_SHEETS_URL}",
            f"CV recommendations doc: {_DOCS_URL}",
            sep,
            "",
            "Good luck today!",
            "Your Job Hunt Agent",
        ]

        body = "\n".join(lines)
        self.gmail.send_email(subject, body)
        logger.info(f"Email sent to {GMAIL_RECIPIENT}")

    # ------------------------------------------------------------------ #
    # Stats
    # ------------------------------------------------------------------ #

    def get_pipeline_stats(self) -> dict:
        rows = self.sheets.get_all_rows()
        counts: dict = {s: 0 for s in ("new", "extracted", "matched", "cv_ready", "weak_match", "irrelevant")}
        strong = good = ok = 0
        for r in rows:
            status = r.get("status", "")
            counts[status] = counts.get(status, 0) + 1
            try:
                score = int(r.get("match_score") or 0)
            except (ValueError, TypeError):
                score = 0
            if score >= 85:
                strong += 1
            elif score >= 70:
                good += 1
            elif score >= 60:
                ok += 1

        return {
            "total_jobs":     len(rows),
            "new":            counts.get("new", 0),
            "extracted":      counts.get("extracted", 0),
            "matched":        counts.get("matched", 0),
            "cv_ready":       counts.get("cv_ready", 0),
            "weak_match":     counts.get("weak_match", 0),
            "irrelevant":     counts.get("irrelevant", 0),
            "strong_matches": strong,
            "good_matches":   good,
            "ok_matches":     ok,
        }

    # ------------------------------------------------------------------ #
    # Scheduler
    # ------------------------------------------------------------------ #

    def schedule_daily(self):
        import schedule as sched

        def _run():
            self.run_date = date.today()
            self.run_pipeline()

        sched.every().day.at("09:00").do(_run)
        logger.info("Scheduled daily run at 9:00 AM")
        while True:
            sched.run_pending()
            time.sleep(60)
