import gspread

from auth.google_auth import get_credentials
from config.settings import GOOGLE_SHEETS_ID

# Full canonical column order. Every agent writes to a subset of these.
# Agents that need sheet data use COLUMNS as the source of truth for column positions.
COLUMNS = [
    "job_id", "company", "role", "url", "jd_raw",
    "posted_date", "location", "status",
    "extracted_skills",
    "match_score", "strong_areas", "weak_areas",
    "doc_tab_url",
]


class SheetsClient:
    def __init__(self):
        creds = get_credentials()
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(GOOGLE_SHEETS_ID)
        self._ws = sh.sheet1
        self._ensure_headers()

    def _ensure_headers(self):
        current = self._ws.row_values(1)
        if current == COLUMNS:
            return
        # Overwrite enough columns to cover both the current width and the
        # target width, so any stale / duplicate columns beyond COLUMNS are
        # cleared rather than left dangling.
        width = max(len(current), len(COLUMNS))
        header_row = COLUMNS + [""] * (width - len(COLUMNS))
        end_col = chr(64 + width) if width <= 26 else "Z"
        self._ws.update(f"A1:{end_col}1", [header_row])

    def append_row(self, row_data: dict):
        row = [row_data.get(col, "") for col in COLUMNS]
        self._ws.append_row(row, value_input_option="RAW")

    def get_all_rows(self) -> list[dict]:
        return self._ws.get_all_records(default_blank="", expected_headers=COLUMNS)

    def row_exists(self, url: str) -> bool:
        if not url:
            return False
        url_col_idx = COLUMNS.index("url") + 1  # gspread is 1-indexed
        col_values = self._ws.col_values(url_col_idx)
        return url in col_values[1:]  # skip header row

    def update_row(self, job_id: str, column_name: str, value: str):
        if column_name not in COLUMNS:
            raise ValueError(f"Unknown column: {column_name!r}")
        col_idx = COLUMNS.index(column_name) + 1  # gspread is 1-indexed
        cell = self._ws.find(job_id, in_column=1)
        if cell is None:
            raise LookupError(f"job_id {job_id!r} not found in sheet")
        self._ws.update_cell(cell.row, col_idx, value)
