import json
import os
import anthropic


EXTRACTION_PROMPT = """You are a contract data extraction specialist for Spyne AI, a SaaS company selling automotive dealership software.

Analyze the following Spyne SaaS contract and extract structured data. Return ONLY valid JSON — no markdown, no explanation.

CONTRACT TEXT:
{contract_text}

Return this exact JSON structure:
{{
  "contract_name": "<Legal Name of the customer/company as written in the contract>",
  "contracted_arr": <total annualized contract value as a plain number, no currency symbols, null if unknown>,
  "num_rooftops": <total number of rooftops/stores in this contract as an integer>,
  "currency": "<USD, EUR, INR, or other — infer from contract>",
  "rooftops": [
    {{
      "rooftop_name": "<specific store/dealership name — see rules below>",
      "products": [
        {{
          "product": "<Studio or Vini>",
          "studio_product": "<Essential, Growth, Pro, Comprehensive, or Lite — null if Vini product>",
          "vini_agents": "<comma-separated agent names from: Inbound Sales, Outbound Sales, Inbound Service, Outbound Service, Chatbot, Service Recall — null if Studio product>",
          "rooftop_product_mrr": <monthly recurring revenue for THIS rooftop + product as plain number, null if unknown>,
          "rooftop_product_arr": <rooftop_product_mrr * 12 as plain number, null if unknown>
        }}
      ]
    }}
  ]
}}

EXTRACTION RULES:

1. MRR CALCULATION:
   - MRR per rooftop = Total Recurring Fee ÷ Billing Frequency Months ÷ Number of Rooftops
   - Example: $900/quarter, 1 rooftop → MRR = 900 ÷ 3 = $300
   - Example: $2400/month, 2 rooftops → MRR per rooftop = 2400 ÷ 2 = $1200

2. CONTRACTED ARR:
   - Total contract annual value = MRR per rooftop × 12 × number of rooftops
   - This is the SAME value repeated in every row for the same contract

3. PRODUCTS:
   - "Studio" = Studio AI, Studio AI Pro, Studio AI Essential, Studio AI Growth, Studio AI Comprehensive, Studio AI Lite, Image Studio
   - "Vini" = Converse AI, Vini AI, AI Agents, any contract mentioning Sales/Service agents with per-agent pricing

4. VINI AGENTS — map to these standard names:
   - Sales Inbound / Inbound Sales → "Inbound Sales"
   - Sales Outbound / Outbound Sales → "Outbound Sales"
   - Service Inbound / Inbound Service → "Inbound Service"
   - Service Outbound / Outbound Service → "Outbound Service"
   - Chatbot → "Chatbot"
   - Service Recall → "Service Recall"
   - If multiple agents: comma-separated, e.g. "Inbound Sales,Outbound Sales"

5. CONTRACTS WITH BOTH STUDIO AND VINI:
   - Create 2 separate product entries for the same rooftop
   - Split the fees: Vini MRR = sum of agent prices listed; Studio MRR = total MRR minus Vini MRR
   - If fee split is unclear, use null for individual MRRs

6. ROOFTOP NAME RULES:
   - Single rooftop, specific store name mentioned (different from Legal Name) → use the store/dealership name
   - Single rooftop, no specific store name → use Legal Name
   - Multiple rooftops with names listed → use each respective name
   - Multiple rooftops WITHOUT individual names → name them "[Legal Name] Rooftop 1", "[Legal Name] Rooftop 2", etc.

7. NON-STANDARD CONTRACTS (e.g. per-image pricing, reseller agreements, no rooftop section):
   - Do your best to extract what you can
   - Return empty rooftops array [] if you truly cannot identify rooftop/product structure
   - Never fabricate data

Return ONLY the JSON object. No other text."""


def parse_contract(contract_text, contract_id, contracted_month, contract_sent_date, contract_signed_date, contract_link):
    """Use Claude to extract structured data from contract text. Returns a list of row dicts."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable not set")

    client = anthropic.Anthropic(api_key=api_key)

    prompt = EXTRACTION_PROMPT.format(contract_text=contract_text[:15000])  # cap at ~15k chars

    message = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Claude returned invalid JSON: {e}\nRaw response: {raw[:500]}")

    CURRENCY_SYMBOLS = {
        "USD": "$", "EUR": "€", "INR": "₹", "GBP": "£",
        "AED": "AED ", "KWD": "KWD ", "JPY": "¥", "BRL": "R$",
    }

    rooftops = data.get("rooftops", [])
    contract_name = data.get("contract_name", "")
    contracted_arr = data.get("contracted_arr", "")
    num_rooftops = data.get("num_rooftops", len(rooftops)) or len(rooftops)
    currency_code = (data.get("currency") or "USD").strip().upper()
    symbol = CURRENCY_SYMBOLS.get(currency_code, currency_code + " ")

    STUDIO_PLANS = {"essential", "growth", "pro", "comprehensive", "lite"}
    VINI_KEYWORDS = {"vini", "converse", "converse ai", "vini ai"}
    STUDIO_KEYWORDS = {"studio", "studio ai", "image studio"}

    # Normalize product values — AI sometimes puts "Vini" in studio_product or leaves product blank
    for rooftop in rooftops:
        for prod in rooftop.get("products", []):
            p  = str(prod.get("product") or "").strip().lower()
            sp = str(prod.get("studio_product") or "").strip().lower()

            if p in VINI_KEYWORDS or sp in VINI_KEYWORDS:
                prod["product"] = "Vini"
                prod["studio_product"] = None
            elif p in STUDIO_KEYWORDS or sp in STUDIO_PLANS:
                prod["product"] = "Studio"
                # keep studio_product as-is (Essential/Growth/Pro/Comprehensive/Lite)
                if sp in VINI_KEYWORDS:
                    prod["studio_product"] = None
            elif prod.get("vini_agents"):
                # has agent names → must be Vini
                prod["product"] = "Vini"
                prod["studio_product"] = None
            elif prod.get("studio_product"):
                prod["product"] = "Studio"

    def _val(v):
        return "" if v is None else v

    def _money(v):
        if v is None or v == "":
            return ""
        try:
            return f"{symbol}{float(v):,.2f}"
        except (ValueError, TypeError):
            return str(v)

    if not rooftops:
        return [{
            "Contract ID": contract_id,
            "Contract Name": contract_name,
            "#Rooftops": _val(num_rooftops),
            "Rooftop Name": "",
            "Contracted ARR": _money(contracted_arr),
            "Product": "",
            "Studio Product": "",
            "Vini Agents": "",
            "Rooftop & Product Level MRR": "",
            "Rooftop & Product Level ARR": "",
            "Contracted Month": contracted_month,
            "Contract Sent Date": contract_sent_date,
            "Contract Signed Date": contract_signed_date,
            "Contract Link": contract_link,
            "_status": "Manual Review Needed",
        }]

    rows = []
    for rooftop in rooftops:
        rooftop_name = rooftop.get("rooftop_name", "")
        for product in rooftop.get("products", []):
            rows.append({
                "Contract ID": contract_id,
                "Contract Name": contract_name,
                "#Rooftops": num_rooftops,
                "Rooftop Name": rooftop_name,
                "Contracted ARR": _money(contracted_arr),
                "Product": product.get("product", ""),
                "Studio Product": _val(product.get("studio_product")),
                "Vini Agents": _val(product.get("vini_agents")),
                "Rooftop & Product Level MRR": _money(product.get("rooftop_product_mrr")),
                "Rooftop & Product Level ARR": _money(product.get("rooftop_product_arr")),
                "Contracted Month": contracted_month,
                "Contract Sent Date": contract_sent_date,
                "Contract Signed Date": contract_signed_date,
                "Contract Link": contract_link,
                "_status": "Processed",
            })

    return rows
