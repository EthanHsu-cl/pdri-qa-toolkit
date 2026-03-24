#!/usr/bin/env python3
"""fetch_from_n8n.py — Call the n8n eBug webhook and save raw JSON to disk.

Usage:
    python scripts/fetch_from_n8n.py
    python scripts/fetch_from_n8n.py --output data/ecl_raw.json
    python scripts/fetch_from_n8n.py --webhook-url https://your-n8n-host/webhook/82746bb5-e140-4720-98a3-d1965900274d

Then feed the output directly into the parser:
    python scripts/parse_ecl_export.py data/ecl_raw.json data/ecl_parsed.csv

Or run both steps in one go:
    python scripts/fetch_from_n8n.py --then-parse
"""

import argparse
import json
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

# ── Configuration ─────────────────────────────────────────────────────────────

DEFAULT_WEBHOOK_URL = (
    "https://ecl-agent.cyberlink.com/webhook-test/82746bb5-e140-4720-98a3-d1965900274d"
)
DEFAULT_OUTPUT   = "data/ecl_raw.json"
DEFAULT_PARSED   = "data/ecl_parsed.csv"

# The n8n workflow reads body.product_name but the query condition is
# hard-coded inside the workflow, so an empty string fetches all products.
DEFAULT_PAYLOAD = {"product_name": "PowerDirector Mobile for iOS"}

# Fields that must be present for the parser to work correctly.
# Build# and Close Build# are nice-to-have; Short Description is critical.
REQUIRED_FIELDS  = {"Short Description", "BugCode", "Severity", "Status", "Create Date"}
OPTIONAL_FIELDS  = {"Build#", "Close Build#", "Version", "Creator"}

# ─────────────────────────────────────────────────────────────────────────────


def fetch_bugs(webhook_url: str, payload: dict, timeout: int = 120) -> list[dict]:
    """POST to the n8n webhook and return a flat list of bug records."""
    body = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    print(f"→ POST {webhook_url}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.reason}")
        body_preview = e.read().decode("utf-8", errors="replace")[:500]
        print(body_preview)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Connection error: {e.reason}")
        print("Check that n8n is running and the webhook URL is correct.")
        sys.exit(1)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Failed to parse JSON response: {e}")
        print("Raw response (first 500 chars):", raw[:500])
        sys.exit(1)

    # n8n "Respond to Webhook → allIncomingItems" wraps each item as
    # {"json": {...}} OR returns a plain list of dicts depending on version.
    # Normalise both shapes into a flat list of bug dicts.
    if isinstance(data, list):
        records = []
        for item in data:
            if isinstance(item, dict) and "json" in item:
                records.append(item["json"])
            elif isinstance(item, dict):
                records.append(item)
        return records
    elif isinstance(data, dict):
        return [data.get("json", data)]
    else:
        print(f"Unexpected response shape: {type(data)}")
        sys.exit(1)


def audit_fields(records: list[dict]) -> bool:
    """Warn about missing fields. Returns True if all required fields present."""
    if not records:
        return False
    sample = records[0]
    keys   = set(sample.keys())

    missing_required = REQUIRED_FIELDS - keys
    missing_optional = OPTIONAL_FIELDS - keys

    if missing_required:
        print(f"\n❌ MISSING REQUIRED fields: {sorted(missing_required)}")
        print("   These are needed for module/tag/severity parsing.")
        print("   Check the 'Get Columns_v3' Set node in your n8n workflow.")
        return False

    if missing_optional:
        print(f"\n⚠️  Missing optional fields: {sorted(missing_optional)}")
        print("   builds_to_fix will be unavailable — everything else will work.")

    print(f"  ✅ All required fields present")
    print(f"  Fields in response: {sorted(keys)}")
    return True


def save_json(records: list[dict], output_path: str):
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    
    existing = []
    p = Path(output_path)
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            existing = json.load(f)
    
    # Deduplicate by BugCode; new records win on conflict
    merged = {r["BugCode"]: r for r in existing}
    merged.update({r["BugCode"]: r for r in records})
    final = list(merged.values())
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False, indent=2)
    
    print(f"✅ Saved {len(final):,} bugs ({len(records):,} new/updated) → {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch eBugs from n8n webhook and save as JSON."
    )
    parser.add_argument(
        "--webhook-url",
        default=DEFAULT_WEBHOOK_URL,
        help=f"n8n webhook POST URL (default: {DEFAULT_WEBHOOK_URL})",
    )
    parser.add_argument(
        "--output", "-o",
        default=DEFAULT_OUTPUT,
        help=f"Output JSON file path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--parsed-output",
        default=DEFAULT_PARSED,
        help=f"Parsed CSV path used by --then-parse (default: {DEFAULT_PARSED})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="HTTP timeout in seconds (default: 120)",
    )
    parser.add_argument(
        "--then-parse",
        action="store_true",
        help="After saving, immediately run parse_ecl_export.py on the result",
    )
    args = parser.parse_args()

    print(f"Fetching eBugs — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    records = fetch_bugs(args.webhook_url, DEFAULT_PAYLOAD, timeout=args.timeout)
    print(f"  Received {len(records):,} records")

    if not records:
        print("⚠️  No records returned. Check the n8n workflow and its date range filter.")
        sys.exit(1)

    ok = audit_fields(records)
    if not ok:
        sys.exit(1)

    save_json(records, args.output)

    if args.then_parse:
        print(f"\nRunning parser → {args.parsed_output}")
        result = subprocess.run(
            [
                sys.executable,
                "scripts/parse_ecl_export.py",
                args.output,
                args.parsed_output,
            ],
            check=False,
        )
        if result.returncode != 0:
            print("Parser exited with errors.")
            sys.exit(result.returncode)
    else:
        print(
            f"\nNext step:\n"
            f"  python scripts/parse_ecl_export.py {args.output} {args.parsed_output}\n"
            f"\nOr re-run with --then-parse to do both in one command."
        )


if __name__ == "__main__":
    main()
