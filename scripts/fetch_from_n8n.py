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
import pandas as pd

# ── Configuration ─────────────────────────────────────────────────────────────

DEFAULT_WEBHOOK_URL = (
    "https://ecl-agent.cyberlink.com/webhook/82746bb5-e140-4720-98a3-d1965900274d-v3"
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


def save_json(records: list[dict], output_path: str) -> dict:
    """Merge incoming records with any existing file, deduplicating by BugCode.

    Returns a summary dict with keys: new, updated_status, unchanged, total.

    Change 4 — Status comparison / update
    ─────────────────────────────────────
    When a record already exists in the cache (same BugCode), its current Status
    is compared with the incoming Status.  If they differ the record is updated
    AND a `_status_changed` flag is set so downstream tools (parse, dashboard)
    can highlight freshly-transitioned bugs without re-processing everything.

    A record that is unchanged on every field still has its `_status_changed`
    flag cleared to False so it is never left incorrectly marked from a
    previous run.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    existing_map: dict = {}
    p = Path(output_path)
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            try:
                for r in json.load(f):
                    if isinstance(r, dict) and r.get("BugCode"):
                        existing_map[r["BugCode"]] = r
            except json.JSONDecodeError:
                print("⚠️  Could not parse existing JSON — treating as empty cache.")

    n_new = n_updated = n_unchanged = 0

    for rec in records:
        code = rec.get("BugCode")
        if not code:
            continue
        if code not in existing_map:
            rec["_status_changed"] = False
            existing_map[code] = rec
            n_new += 1
        else:
            old = existing_map[code]
            old_status = str(old.get("Status", "")).strip().lower()
            new_status = str(rec.get("Status", "")).strip().lower()
            if old_status != new_status:
                rec["_status_changed"] = True
                rec["_prev_status"]    = old.get("Status", "")
                existing_map[code]     = rec
                n_updated += 1
            else:
                # Always reset flag so a record from a previous run is not
                # stuck with _status_changed=True after it stabilises.
                rec["_status_changed"] = False
                existing_map[code]     = rec
                n_unchanged += 1

    final = list(existing_map.values())

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(final, f, ensure_ascii=False, indent=2)

    summary = dict(new=n_new, updated_status=n_updated, unchanged=n_unchanged, total=len(final))
    print(
        f"✅ Saved {summary['total']:,} bugs → {output_path}\n"
        f"   New: {n_new}  |  Status-changed: {n_updated}  |  Unchanged: {n_unchanged}"
    )
    return summary


def get_latest_version(output_path: str) -> str:
    """Read version_catalogue.csv (same directory as output_path) and return
    the most recent non-sparse version string.

    This is used when scope=latest so the n8n workflow receives the actual
    version string to filter on, not the bare word 'latest'.

    Returns an empty string if the catalogue is absent or unreadable — in
    that case the workflow falls back to its own 'latest' logic.
    """
    cat_path = Path(output_path).parent / "version_catalogue.csv"
    if not cat_path.exists():
        return ""
    try:
        cat = pd.read_csv(str(cat_path))
        real = cat[~cat["version_is_sparse"].astype(bool)].sort_values("version_rank")
        if not real.empty:
            ver = str(real.iloc[0]["parsed_version"])
            print(f"  Latest non-sparse version from catalogue: {ver}")
            return ver
    except Exception as e:
        print(f"  Could not read version catalogue ({e}) — n8n will decide 'latest'.")
    return ""


def resolve_scope(args) -> str:
    """Change 5 — Testing-schedule experiment.

    Weekdays (Mon–Fri): fetch only the latest version (faster, lighter).
    Weekends (Sat–Sun): fetch all versions to measure full-update duration.

    Override with --scope latest | all to force either mode regardless of day.
    """
    if args.scope == "latest":
        return "latest"
    if args.scope == "all":
        return "all"
    # auto: weekday → latest, weekend → all
    today = datetime.now().weekday()   # 0=Mon … 6=Sun
    if today >= 5:
        print(f"📅 Weekend detected (weekday={today}) → fetching ALL versions")
        return "all"
    else:
        print(f"📅 Weekday detected (weekday={today}) → fetching LATEST version only")
        return "latest"


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
    # Change 5 — scope control
    parser.add_argument(
        "--scope",
        choices=["auto", "latest", "all"],
        default="auto",
        help=(
            "Fetch scope: 'latest' = newest version only (fast, weekday default); "
            "'all' = all versions (thorough, weekend default); "
            "'auto' = choose by day of week (default)."
        ),
    )
    args = parser.parse_args()

    scope = resolve_scope(args)
    start_time = datetime.now()
    print(f"Fetching eBugs — {start_time.strftime('%Y-%m-%d %H:%M:%S')}  [scope={scope}]")

    # Build the payload; when scope=latest, resolve the actual version string
    # from the catalogue so the n8n workflow knows exactly which version to
    # fetch rather than guessing by string sort order.
    payload = dict(DEFAULT_PAYLOAD)
    payload["scope"] = scope
    if scope == "latest":
        latest_ver = get_latest_version(args.output)
        if latest_ver:
            payload["latest_version"] = latest_ver
            print(f"  Sending latest_version={latest_ver!r} in webhook payload")

    records = fetch_bugs(args.webhook_url, payload, timeout=args.timeout)
    print(f"  Received {len(records):,} records")

    if not records:
        print("⚠️  No records returned. Check the n8n workflow and its date range filter.")
        sys.exit(1)

    ok = audit_fields(records)
    if not ok:
        sys.exit(1)

    summary = save_json(records, args.output)

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"⏱  Fetch+save completed in {elapsed:.1f}s  (scope={scope})")

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