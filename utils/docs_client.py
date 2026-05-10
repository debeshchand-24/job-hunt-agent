from loguru import logger
from googleapiclient.discovery import build

from auth.google_auth import get_credentials
from config.settings import GOOGLE_DOCS_ID


class DocsClient:
    def __init__(self):
        creds = get_credentials()
        self._service    = build("docs", "v1", credentials=creds)
        self._documents  = self._service.documents()
        self._doc_id     = GOOGLE_DOCS_ID

    # ------------------------------------------------------------------ #
    # Tab management
    # ------------------------------------------------------------------ #

    def create_tab(self, tab_title: str) -> str:
        """Create a new tab in the document and return its tab_id.

        Falls back to appending a section header to the main body if the
        Docs API does not support tabs for this document, returning the
        sentinel '__main_body__' so callers can branch accordingly.
        """
        # Truncate title to avoid API rejection on very long names
        title = tab_title[:60]
        try:
            result = self._documents.batchUpdate(
                documentId=self._doc_id,
                body={"requests": [{"createTab": {"tabProperties": {"title": title}}}]},
            ).execute()
            for reply in result.get("replies", []):
                if "createTab" in reply:
                    tab_id = reply["createTab"]["tabProperties"]["tabId"]
                    logger.info(f"Created tab '{title}' (id={tab_id})")
                    return tab_id
            raise RuntimeError("createTab reply missing tabId")
        except Exception as exc:
            logger.warning(f"Tab creation failed ({exc}) — falling back to main body section")
            return "__main_body__"

    def write_to_tab(self, tab_id: str, content: str):
        """Write formatted content to the given tab (or main body fallback).

        Markdown-style headers (##, ###) are stripped of their prefix and
        rendered as bold text. Plain lines are inserted as-is.
        """
        if tab_id == "__main_body__":
            self._append_to_main_body(content)
            return

        lines = content.split("\n")
        processed: list[str] = []
        bold_ranges: list[tuple[int, int]] = []
        pos = 1  # Google Docs positions start at 1

        for line in lines:
            if line.startswith("## "):
                clean = line[3:]
            elif line.startswith("### "):
                clean = line[4:]
            elif line.startswith("# "):
                clean = line[2:]
            else:
                clean = line

            if clean != line:  # it was a header
                bold_ranges.append((pos, pos + len(clean)))

            processed.append(clean)
            pos += len(clean) + 1  # +1 for the trailing newline

        full_text = "\n".join(processed) + "\n"

        requests: list[dict] = [
            {
                "insertText": {
                    "location": {"tabId": tab_id, "index": 1},
                    "text": full_text,
                }
            }
        ]
        for start, end in bold_ranges:
            requests.append({
                "updateTextStyle": {
                    "textStyle": {"bold": True},
                    "fields": "bold",
                    "range": {
                        "tabId": tab_id,
                        "startIndex": start,
                        "endIndex": end,
                    },
                }
            })

        self._documents.batchUpdate(
            documentId=self._doc_id,
            body={"requests": requests},
        ).execute()
        logger.debug(f"Wrote {len(full_text):,} chars to tab {tab_id}")

    def _append_to_main_body(self, content: str):
        """Fallback: append a visibly separated section to the main document body."""
        doc     = self._documents.get(documentId=self._doc_id).execute()
        end_idx = doc["body"]["content"][-1]["endIndex"] - 1

        separator = "\n" + ("=" * 60) + "\n"
        full_text = separator + content + "\n"

        self._documents.batchUpdate(
            documentId=self._doc_id,
            body={"requests": [
                {"insertText": {
                    "location": {"index": end_idx},
                    "text": full_text,
                }}
            ]},
        ).execute()

    # ------------------------------------------------------------------ #
    # URL helpers
    # ------------------------------------------------------------------ #

    def get_doc_url(self, tab_id: str) -> str:
        base = f"https://docs.google.com/document/d/{self._doc_id}/edit"
        if tab_id == "__main_body__":
            return base
        return f"{base}?tab=t.{tab_id}"
