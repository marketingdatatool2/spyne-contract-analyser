import json
import queue
import threading

from flask import Flask, jsonify, render_template, request, Response

from utils.contract_parser import parse_contract
from utils.drive_fetcher import fetch_contract_text
from utils.sheets_manager import (
    get_sheet_stats,
    get_unprocessed_contracts,
    update_raw_status,
    write_to_analyser,
)

app = Flask(__name__)

_progress_queue: queue.Queue = queue.Queue()
_is_processing = False
_processing_lock = threading.Lock()


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/stats")
def stats():
    try:
        data = get_sheet_stats()
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/analyze", methods=["POST"])
def analyze():
    global _is_processing
    with _processing_lock:
        if _is_processing:
            return jsonify({"error": "Analysis already in progress"}), 409
        _is_processing = True

    # Clear old messages
    while not _progress_queue.empty():
        try:
            _progress_queue.get_nowait()
        except queue.Empty:
            break

    t = threading.Thread(target=_run_analysis, daemon=True)
    t.start()
    return jsonify({"message": "Analysis started"})


@app.route("/api/stream")
def stream():
    """Server-Sent Events endpoint for live progress."""
    def generate():
        while True:
            try:
                msg = _progress_queue.get(timeout=25)
                yield f"data: {json.dumps(msg)}\n\n"
                if msg.get("type") == "done":
                    break
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Background worker ─────────────────────────────────────────────────────────

def _emit(msg: dict):
    _progress_queue.put(msg)


def _run_analysis():
    global _is_processing
    try:
        contracts = get_unprocessed_contracts()
        total = len(contracts)

        if total == 0:
            _emit({"type": "done", "message": "No new contracts to process."})
            return

        _emit({"type": "start", "message": f"Found {total} unprocessed contract(s).", "total": total})

        success_count = 0
        fail_count = 0

        for i, contract in enumerate(contracts, 1):
            cid = contract["contract_id"]
            link = contract["contract_link"]

            _emit({"type": "processing", "message": f"[{i}/{total}] Contract {cid}", "current": i, "total": total})

            try:
                update_raw_status(contract["row_num"], "Processing")

                _emit({"type": "log", "message": "  → Fetching contract document..."})
                text = fetch_contract_text(link)

                _emit({"type": "log", "message": "  → Extracting data with AI..."})
                rows = parse_contract(
                    contract_text=text,
                    contract_id=cid,
                    contracted_month=contract["contracted_month"],
                    contract_sent_date=contract["contract_sent_date"],
                    contract_signed_date=contract["contract_signed_date"],
                    contract_link=link,
                )

                _emit({"type": "log", "message": f"  → Writing {len(rows)} row(s) to sheet..."})
                write_to_analyser(rows)

                final_status = rows[0].get("_status", "Processed") if rows else "Processed"
                update_raw_status(contract["row_num"], final_status)

                if final_status == "Manual Review Needed":
                    _emit({"type": "warning", "message": f"  ⚠ Contract {cid} needs manual review — {len(rows)} row(s) written.", "current": i, "total": total})
                else:
                    _emit({"type": "success", "message": f"  ✓ Contract {cid} done — {len(rows)} row(s) written.", "current": i, "total": total})
                    success_count += 1

            except Exception as exc:
                update_raw_status(contract["row_num"], "Failed")
                _emit({"type": "error", "message": f"  ✗ Contract {cid} failed: {exc}", "current": i, "total": total})
                fail_count += 1

        summary = f"Done. {success_count} succeeded, {fail_count} failed out of {total} contract(s)."
        _emit({"type": "done", "message": summary})

    except Exception as exc:
        _emit({"type": "error", "message": f"Fatal error: {exc}"})
        _emit({"type": "done", "message": "Analysis stopped due to a fatal error."})
    finally:
        _is_processing = False


if __name__ == "__main__":
    app.run(debug=True, port=5000)
