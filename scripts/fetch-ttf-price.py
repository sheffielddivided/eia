#!/usr/bin/env python3
"""Fetch the EEX TTF Neutral Gas Price (day-ahead spot, EUR/MWh) and
accumulate it into data/ttf_price.json.

The source CSV (gasandregistry.eex.com) only ever exposes the most recent
60 days, so each run merges that window into the persisted archive instead
of overwriting it -- the archive grows by roughly one day per run and never
loses history older than 60 days, as long as this runs at least once every
60 days.
"""
import csv
import io
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TTF_URL = 'https://gasandregistry.eex.com/Gas/NGP/TTF_NGP_60_Days.csv'
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / 'data'
OUT_FILE = DATA_DIR / 'ttf_price.json'


def fetch_csv(attempt=0):
    req = urllib.request.Request(TTF_URL, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; eugas-data-fetch/1.0)',
        'Accept': 'text/csv',
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read().decode('utf-8-sig')  # utf-8-sig strips the BOM
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        if attempt < 3:
            wait = 2 ** (attempt + 1)
            print(f'  retry {attempt + 1} after {wait}s: {e}', file=sys.stderr)
            time.sleep(wait)
            return fetch_csv(attempt + 1)
        raise


def parse_csv(text):
    """{'YYYY-MM-DD': price_float} from the 'Delivery date;Index Value (EUR/MWh);...' rows."""
    reader = csv.reader(io.StringIO(text), delimiter=';')
    rows = list(reader)
    prices = {}
    for row in rows[1:]:  # skip header row
        if len(row) < 3:
            continue
        delivery_date = row[1].strip()
        value_str = row[2].strip()
        if not delivery_date or not value_str:
            continue
        try:
            prices[delivery_date] = float(value_str)
        except ValueError:
            continue
    return prices


def main():
    text = fetch_csv()
    new_prices = parse_csv(text)
    print(f'Fetched {len(new_prices)} days from EEX TTF CSV')
    if not new_prices:
        sys.exit('No rows parsed from TTF CSV -- refusing to overwrite archive with empty data.')

    existing = {}
    if OUT_FILE.exists():
        try:
            existing = json.loads(OUT_FILE.read_text()).get('prices', {})
        except Exception:
            existing = {}

    merged = {**existing, **new_prices}  # new data wins on overlapping dates (late revisions)
    added = len(merged) - len(existing)

    out = {
        'updatedAt': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'prices': dict(sorted(merged.items())),
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(out, separators=(',', ':')))
    print(f'Wrote {OUT_FILE}: {len(merged)} total days (was {len(existing)}, +{added} new)')


if __name__ == '__main__':
    main()
