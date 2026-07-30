# Spyne Contract Analyser

An internal tool that reads Spyne SaaS contracts (PDFs / Google Docs) from a Google Sheet, extracts rooftop-level data using Claude AI, and writes structured output back into the same sheet.

---

## Business Context

**Spyne AI** sells two product lines to automotive dealerships:
- **Studio** (Studio AI) — image/360° photography automation. Variants: Essential, Growth, Pro, Comprehensive, Lite.
- **Vini** (Converse AI / Vini AI) — AI voice agents for dealerships. Agent types: Inbound Sales, Outbound Sales, Inbound Service, Outbound Service, Chatbot, Service Recall.

A **Rooftop** = one physical dealership location. A single contract can cover multiple rooftops (e.g. a dealer group with 5 stores = 5 rooftops under one contract).

The goal of this tool is to maintain a clean **rooftop-level contracted data table** from signed contracts — one row per rooftop per product — to be used for revenue tracking and enrichment via Clay.

---

## Google Sheet

**Sheet ID:** `1Xeje9A3yuXtuBQyrTkSKK-1RM6w5GBMzxUzVDlSIYZo`
**Sheet URL:** https://docs.google.com/spreadsheets/d/1Xeje9A3yuXtuBQyrTkSKK-1RM6w5GBMzxUzVDlSIYZo/edit

### Tab 1 — `Contract Raw` (INPUT)

The tool reads from this tab. Filled manually by the team.

| Column | Description |
|--------|-------------|
| Contract ID | Unique ID (e.g. JAN2526003). The tool matches status by this column — not row number. |
| Agreement Month | Month the contract was signed (e.g. Jan'26) |
| Agreement Sent Date | Date contract was sent to client |
| Agreement Sign Date | Date contract was signed |
| Agreement Link | Google Drive link to the PDF or Google Doc (must be publicly accessible) |
| Status | Auto-filled by the tool: `Processed`, `Failed`, `Processing`, `Manual Review Needed`, `Duplicate` |

**Status values explained:**
- `Processed` — successfully extracted and written to Contract Analyser
- `Failed` — an error occurred (tool will retry on next run)
- `Processing` — currently being processed (clears to Processed/Failed on completion)
- `Manual Review Needed` — tool could not extract enough data; a partial row is written to Analyser
- `Duplicate` — same Contract ID already processed in another row; auto-skipped

### Tab 2 — `Contract Analyser` (OUTPUT)

The tool writes to this tab. One row per rooftop per product. **Do not edit column A–N headers — they are auto-managed by the tool.**

| Column | Description |
|--------|-------------|
| Contract ID | From Contract Raw |
| Contract Name | Legal name of the customer/company from the contract |
| #Rooftops | Total number of rooftops in the contract (integer) |
| Rooftop Name | Specific store/dealership name |
| Contracted ARR | Total annualized contract value (all rooftops × 12 months), with currency symbol |
| Product | `Studio` or `Vini` |
| Studio Product | Plan name: Essential / Growth / Pro / Comprehensive / Lite (blank for Vini) |
| Vini Agents | Comma-separated agent names e.g. `Inbound Sales,Outbound Sales` (blank for Studio) |
| Rooftop & Product Level MRR | Monthly recurring revenue for this specific rooftop + product, with currency symbol |
| Rooftop & Product Level ARR | MRR × 12, with currency symbol |
| Contracted Month | Copied from Contract Raw |
| Contract Sent Date | Copied from Contract Raw |
| Contract Signed Date | Copied from Contract Raw |
| Contract Link | Copied from Contract Raw |

**Key rules the tool follows:**
- One row per rooftop per product. If a contract has 2 rooftops and 1 product → 2 rows. If 1 rooftop and 2 products (Studio + Vini) → 2 rows.
- If a Contract ID already has rows in Contract Analyser, the tool skips it (protects manual edits).
- If a field cannot be extracted, it is left blank.
- Currency is detected from the contract (USD, EUR, INR, etc.) and prefixed to all monetary values.

### Tab 3 — `Clay Analyser` (FUTURE)

Reserved for Clay enrichment output. Not yet connected.

---

## MRR / ARR Calculation Logic

```
MRR per rooftop = Total Recurring Fee ÷ Billing Frequency Months ÷ Number of Rooftops
ARR per rooftop = MRR × 12
Contracted ARR  = MRR per rooftop × 12 × Number of Rooftops  (= total contract annual value)
```

**Examples:**
- EV Car Company: €900/quarter, 1 rooftop → MRR = €900÷3 = €300, ARR = €3,600
- Germain of Beavercreek: $2,400/month, 2 rooftops → MRR per rooftop = $1,200, ARR per rooftop = $14,400, Contracted ARR = $28,800
- Contract with both Studio + Vini: fee is split by reading individual agent prices from the contract

---

## Contract Format

Most contracts follow a standard **Spyne SaaS Services Order Form** template:
- Customer Details section: Legal Name, number of Rooftops
- Billing Details section: Product & Plan Name, Total Recurring Fee, Billing Frequency
- Additional Details section: sometimes lists individual rooftop names, agent pricing breakdown

Some contracts are non-standard (different language, per-image pricing, reseller agreements). The tool does best-effort extraction and marks them `Manual Review Needed` if it cannot determine rooftop/product structure.

**Rooftop Name rules:**
- Single rooftop, specific store name found (different from Legal Name) → use store name
- Single rooftop, no specific store name → use Legal Name
- Multiple rooftops with names listed → use each respective name
- Multiple rooftops without individual names → `[Legal Name] Rooftop 1`, `[Legal Name] Rooftop 2`, etc.

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python 3, Flask |
| AI Extraction | Anthropic Claude API (`claude-sonnet-4-5`) |
| Google Sheets | `gspread` + Google Service Account |
| PDF Parsing | `pdfplumber` |
| Frontend | Plain HTML/CSS/JS with SSE for live progress |
| Hosting | Railway (auto-deploys from GitHub on push) |
| Repo | https://github.com/marketingdatatool2/spyne-contract-analyser |

---

## Project Structure

```
spyne-contract-analyser/
├── app.py                    # Flask app, SSE streaming, background worker
├── requirements.txt
├── Procfile                  # gunicorn for Railway
├── railway.toml
├── .env.example
├── templates/
│   └── index.html            # Dashboard UI
└── utils/
    ├── drive_fetcher.py      # Downloads PDF/Doc from public Google Drive URL
    ├── contract_parser.py    # Claude AI extraction + field normalization
    └── sheets_manager.py     # Google Sheets read/write
```

---

## Environment Variables (Railway)

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Claude API key from console.anthropic.com |
| `GOOGLE_SHEET_ID` | `1Xeje9A3yuXtuBQyrTkSKK-1RM6w5GBMzxUzVDlSIYZo` |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Full JSON content of the Google service account key file |

**Service account email** (share the Google Sheet with this as Editor):
`spyne-analyser@spyne-contract-analyser.iam.gserviceaccount.com`

---

## Live Tool URL

https://web-production-5da0c.up.railway.app/

---

## How to Use

1. Add new contract rows to the `Contract Raw` tab (Contract ID, dates, Agreement Link)
2. Open the tool URL
3. Click **Analyze Contracts**
4. Watch the live activity log — each contract is fetched, parsed by AI, and written row-by-row
5. Check `Contract Analyser` tab for the output

**The tool only processes rows where Status is blank or `Failed`. It never re-processes `Processed` rows.**

---

## Known Behaviours & Edge Cases

- **Duplicate Contract IDs** in Contract Raw: the first row is processed, all subsequent rows with the same ID are auto-marked `Duplicate` and skipped
- **Failed rows** are retried on the next run (not terminal)
- **Multi-product contracts** (e.g. Austin Ford CDJR with both Studio and Vini): the tool writes 2 rows for the same rooftop, one per product, and splits the fee based on agent prices listed in the contract
- **Non-USD contracts**: currency is auto-detected and prefixed (€, ₹, £, etc.)
- **Non-standard contracts** (e.g. Indian resellers, per-image pricing): partially extracted, marked `Manual Review Needed`
- **Manually filled cells** in Contract Analyser are protected — if a Contract ID already exists in the tab, the tool will not overwrite it

---

## Planned: Clay Integration

The `Clay Analyser` tab is reserved for the next phase. Plan:
- Export rooftop data from `Contract Analyser` to Clay
- Clay enriches with firmographic data (company size, tech stack, contacts, etc.)
- Enriched output lands back in `Clay Analyser` tab

Details to be defined when Clay integration begins.

---

## Development Notes

- 1 gunicorn worker (required — SSE uses in-process queue, multiple workers would break it)
- Gunicorn timeout is 300s to handle large contract batches
- Contract text is capped at 15,000 chars before sending to Claude (covers all standard contracts)
- The tool fetches contracts by converting Drive share URLs to direct download URLs
- Google Drive large-file confirmation is handled automatically
