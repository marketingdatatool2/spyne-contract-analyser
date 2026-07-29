import json
import os
import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

ANALYSER_COLUMNS = [
    "Contract ID",
    "Contract Name",
    "#Rooftops",
    "Rooftop Name",
    "Contracted ARR",
    "Product",
    "Studio Product",
    "Vini Agents",
    "Rooftop & Product Level MRR",
    "Rooftop & Product Level ARR",
    "Contracted Month",
    "Contract Sent Date",
    "Contract Signed Date",
    "Contract Link",
]

RAW_COLUMN_MAP = {
    "contract_id":          "Contract ID",
    "contracted_month":     "Agreement Month",
    "contract_sent_date":   "Agreement Sent Date",
    "contract_signed_date": "Agreement Sign Date",
    "contract_link":        "Agreement Link",
}


def _get_client():
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not sa_json:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON not set")
    creds = Credentials.from_service_account_info(json.loads(sa_json), scopes=SCOPES)
    return gspread.authorize(creds)


def _open_sheet():
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise ValueError("GOOGLE_SHEET_ID not set")
    return _get_client().open_by_key(sheet_id)


def _find_col(headers, name):
    try:
        return headers.index(name) + 1
    except ValueError:
        return None


# ── Contract Raw ──────────────────────────────────────────────────────────────

def get_unprocessed_contracts():
    sh = _open_sheet()
    raw = sh.worksheet("Contract Raw")
    headers = raw.row_values(1)

    if "Status" not in headers:
        raw.update_cell(1, len(headers) + 1, "Status")
        headers.append("Status")

    all_values = raw.get_all_values()
    if len(all_values) <= 1:
        return []

    terminal = {"Processed", "Processing", "Manual Review Needed", "Duplicate"}

    def cell(col_name, row):
        idx = _find_col(headers, col_name)
        return str(row[idx - 1]).strip() if idx and idx <= len(row) else ""

    # Collect Contract IDs already in a terminal state
    done_ids = {
        cell(RAW_COLUMN_MAP["contract_id"], row)
        for row in all_values[1:]
        if cell("Status", row) in terminal
    }

    contracts = []
    seen_in_batch = set()
    rows_to_mark_duplicate = []

    for row_num, row in enumerate(all_values[1:], start=2):
        status = cell("Status", row)
        if status in terminal:
            continue

        cid   = cell(RAW_COLUMN_MAP["contract_id"], row)
        link  = cell(RAW_COLUMN_MAP["contract_link"], row)
        if not cid or not link:
            continue

        # Duplicate: same Contract ID already processed or already queued this run
        if cid in done_ids or cid in seen_in_batch:
            rows_to_mark_duplicate.append((row_num, cid))
            continue

        seen_in_batch.add(cid)
        contracts.append({
            "row_num":              row_num,
            "contract_id":          cid,
            "contract_link":        link,
            "contracted_month":     cell(RAW_COLUMN_MAP["contracted_month"], row),
            "contract_sent_date":   cell(RAW_COLUMN_MAP["contract_sent_date"], row),
            "contract_signed_date": cell(RAW_COLUMN_MAP["contract_signed_date"], row),
        })

    # Immediately mark duplicates so they never show as Pending again
    for row_num, cid in rows_to_mark_duplicate:
        _set_status_by_row(raw, headers, row_num, "Duplicate")

    return contracts


def update_raw_status(contract_id, status):
    sh = _open_sheet()
    raw = sh.worksheet("Contract Raw")
    headers = raw.row_values(1)

    if "Status" not in headers:
        raw.update_cell(1, len(headers) + 1, "Status")
        headers.append("Status")

    id_col_idx = _find_col(headers, RAW_COLUMN_MAP["contract_id"])
    if not id_col_idx:
        return

    for row_num, row in enumerate(raw.get_all_values()[1:], start=2):
        if len(row) >= id_col_idx and str(row[id_col_idx - 1]).strip() == str(contract_id).strip():
            _set_status_by_row(raw, headers, row_num, status)
            return


def _set_status_by_row(raw_ws, headers, row_num, status):
    status_col = _find_col(headers, "Status")
    if not status_col:
        status_col = len(headers) + 1
        raw_ws.update_cell(1, status_col, "Status")
    raw_ws.update_cell(row_num, status_col, status)


# ── Contract Analyser ─────────────────────────────────────────────────────────

def write_to_analyser(rows):
    """Write rows to Contract Analyser. Always ensures headers are in row 1.
    Skips any contract whose ID already exists in the sheet (protects manual edits).
    Returns the number of rows actually written.
    """
    if not rows:
        return 0

    sh = _open_sheet()
    ws = sh.worksheet("Contract Analyser")
    all_values = ws.get_all_values()

    # Always ensure row 1 = expected headers
    if not all_values or all_values[0] != ANALYSER_COLUMNS:
        ws.update(values=[ANALYSER_COLUMNS], range_name="A1")
        all_values = ws.get_all_values()

    # Build set of Contract IDs already in sheet (rows 2+)
    id_col = ANALYSER_COLUMNS.index("Contract ID")
    existing_ids = {
        str(row[id_col]).strip()
        for row in all_values[1:]
        if len(row) > id_col and str(row[id_col]).strip()
    }

    # Write all rows for contracts NOT already in sheet
    # (multiple rows per contract — e.g. Studio + Vini — are all included)
    rows_to_write = [
        [str(row_data.get(col, "") or "") for col in ANALYSER_COLUMNS]
        for row_data in rows
        if str(row_data.get("Contract ID", "")).strip() not in existing_ids
    ]

    if rows_to_write:
        ws.append_rows(rows_to_write, value_input_option="USER_ENTERED")

    return len(rows_to_write)


# ── Stats ─────────────────────────────────────────────────────────────────────

def get_sheet_stats():
    sh = _open_sheet()
    raw = sh.worksheet("Contract Raw")
    headers = raw.row_values(1)
    all_values = raw.get_all_values()

    total = max(0, len(all_values) - 1)
    processed = failed = manual = pending = duplicate = 0

    def cell(col_name, row):
        idx = _find_col(headers, col_name)
        return str(row[idx - 1]).strip() if idx and idx <= len(row) else ""

    for row in all_values[1:]:
        s = cell("Status", row)
        if s == "Processed":        processed += 1
        elif s == "Failed":         failed += 1
        elif s == "Manual Review Needed": manual += 1
        elif s == "Duplicate":      duplicate += 1
        else:                       pending += 1

    return {
        "total":         total,
        "processed":     processed,
        "pending":       pending,
        "failed":        failed,
        "manual_review": manual,
        "duplicate":     duplicate,
    }
