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
    "contract_id":            "Contract ID",
    "contracted_month":       "Agreement Month",
    "contract_sent_date":     "Agreement Sent Date",
    "contract_signed_date":   "Agreement Sign Date",
    "contract_link":          "Agreement Link",
}


def _get_client():
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not sa_json:
        raise ValueError("GOOGLE_SERVICE_ACCOUNT_JSON environment variable not set")
    creds_dict = json.loads(sa_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)


def _open_sheet():
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not sheet_id:
        raise ValueError("GOOGLE_SHEET_ID environment variable not set")
    gc = _get_client()
    return gc.open_by_key(sheet_id)


def _find_col(headers, name):
    """Return 1-based column index, or None."""
    try:
        return headers.index(name) + 1
    except ValueError:
        return None


# ── Contract Raw helpers ──────────────────────────────────────────────────────

def get_unprocessed_contracts():
    sh = _open_sheet()
    raw = sh.worksheet("Contract Raw")
    headers = raw.row_values(1)

    # Ensure Status column exists
    if "Status" not in headers:
        status_col_idx = len(headers) + 1
        raw.update_cell(1, status_col_idx, "Status")
        headers.append("Status")

    all_values = raw.get_all_values()
    if len(all_values) <= 1:
        return []

    terminal_statuses = {"Processed", "Processing", "Manual Review Needed"}

    contracts = []
    for row_num, row in enumerate(all_values[1:], start=2):
        def cell(col_name):
            idx = _find_col(headers, col_name)
            if idx and idx <= len(row):
                return str(row[idx - 1]).strip()
            return ""

        status = cell("Status")
        if status in terminal_statuses:
            continue

        contract_id = cell(RAW_COLUMN_MAP["contract_id"])
        contract_link = cell(RAW_COLUMN_MAP["contract_link"])

        if not contract_id or not contract_link:
            continue

        contracts.append({
            "row_num": row_num,
            "contract_id": contract_id,
            "contract_link": contract_link,
            "contracted_month": cell(RAW_COLUMN_MAP["contracted_month"]),
            "contract_sent_date": cell(RAW_COLUMN_MAP["contract_sent_date"]),
            "contract_signed_date": cell(RAW_COLUMN_MAP["contract_signed_date"]),
        })

    return contracts


def update_raw_status(row_num, status):
    sh = _open_sheet()
    raw = sh.worksheet("Contract Raw")
    headers = raw.row_values(1)

    if "Status" not in headers:
        status_col_idx = len(headers) + 1
        raw.update_cell(1, status_col_idx, "Status")
    else:
        status_col_idx = headers.index("Status") + 1

    raw.update_cell(row_num, status_col_idx, status)


# ── Contract Analyser helpers ─────────────────────────────────────────────────

def _ensure_analyser_headers(ws):
    """Write column headers if the sheet is empty."""
    existing = ws.row_values(1)
    if not existing:
        ws.update("A1", [ANALYSER_COLUMNS])
    return ws.row_values(1)


def _get_existing_contract_ids(ws, headers):
    """Return a set of Contract IDs already present in the Analyser tab."""
    try:
        col_idx = headers.index("Contract ID")
    except ValueError:
        return set()

    all_values = ws.get_all_values()
    ids = set()
    for row in all_values[1:]:
        if len(row) > col_idx:
            val = str(row[col_idx]).strip()
            if val:
                ids.add(val)
    return ids


def write_to_analyser(rows):
    """Append rows to Contract Analyser. Skips if Contract ID already exists."""
    if not rows:
        return

    sh = _open_sheet()
    ws = sh.worksheet("Contract Analyser")
    headers = _ensure_analyser_headers(ws)
    existing_ids = _get_existing_contract_ids(ws, headers)

    rows_to_write = []
    for row_data in rows:
        cid = str(row_data.get("Contract ID", "")).strip()
        if cid in existing_ids:
            continue  # Protect manually edited rows
        row_values = [str(row_data.get(col, "") or "") for col in ANALYSER_COLUMNS]
        rows_to_write.append(row_values)
        existing_ids.add(cid)  # Avoid duplicates within this batch

    if rows_to_write:
        ws.append_rows(rows_to_write, value_input_option="USER_ENTERED")


# ── Stats helper ──────────────────────────────────────────────────────────────

def get_sheet_stats():
    sh = _open_sheet()
    raw = sh.worksheet("Contract Raw")
    headers = raw.row_values(1)
    all_values = raw.get_all_values()

    total = max(0, len(all_values) - 1)
    processed = 0
    failed = 0
    manual = 0
    pending = 0

    terminal = {"Processed", "Manual Review Needed"}
    for row in all_values[1:]:
        status_idx = _find_col(headers, "Status")
        status = str(row[status_idx - 1]).strip() if status_idx and status_idx <= len(row) else ""
        if status == "Processed":
            processed += 1
        elif status == "Failed":
            failed += 1
        elif status == "Manual Review Needed":
            manual += 1
        else:
            pending += 1

    return {
        "total": total,
        "processed": processed,
        "pending": pending,
        "failed": failed,
        "manual_review": manual,
    }
