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


def update_raw_status(contract_id, status):
    sh = _open_sheet()
    raw = sh.worksheet("Contract Raw")
    headers = raw.row_values(1)

    if "Status" not in headers:
        status_col_idx = len(headers) + 1
        raw.update_cell(1, status_col_idx, "Status")
        headers.append("Status")
    else:
        status_col_idx = headers.index("Status") + 1

    id_col_idx = _find_col(headers, RAW_COLUMN_MAP["contract_id"])
    if not id_col_idx:
        return

    all_values = raw.get_all_values()
    for row_num, row in enumerate(all_values[1:], start=2):
        if len(row) >= id_col_idx and str(row[id_col_idx - 1]).strip() == str(contract_id).strip():
            raw.update_cell(row_num, status_col_idx, status)
            return


# ── Contract Analyser helpers ─────────────────────────────────────────────────

def write_to_analyser(rows):
    """Append rows to Contract Analyser.

    Skips all rows for a Contract ID that already has ANY entry in the sheet
    (protects manual edits). Multiple rows with the same Contract ID (multi-rooftop
    or multi-product contracts) are all written together.
    """
    if not rows:
        return

    sh = _open_sheet()
    ws = sh.worksheet("Contract Analyser")

    # Read current sheet state once
    all_values = ws.get_all_values()

    # Write headers if sheet is empty
    if not all_values:
        ws.append_row(ANALYSER_COLUMNS, value_input_option="USER_ENTERED")
        existing_ids = set()
    else:
        # Find Contract ID column index from existing headers
        existing_headers = all_values[0]
        try:
            id_col = existing_headers.index("Contract ID")
        except ValueError:
            id_col = 0
        existing_ids = {
            str(row[id_col]).strip()
            for row in all_values[1:]
            if len(row) > id_col and str(row[id_col]).strip()
        }

    # Build rows to write — skip entire contract if ANY row already exists in sheet
    rows_to_write = [
        [str(row_data.get(col, "") or "") for col in ANALYSER_COLUMNS]
        for row_data in rows
        if str(row_data.get("Contract ID", "")).strip() not in existing_ids
    ]

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
