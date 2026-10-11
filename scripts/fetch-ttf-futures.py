#!/usr/bin/env python3
"""Fetch TTF=F (Dutch TTF Natural Gas front-month futures, EUR/MWh) daily
closes from Yahoo Finance's chart endpoint and accumulate into
data/ttf_futures.json.

Unlike the EEX day-ahead spot CSV (which only exposes a rolling 60-day
window and must be accumulated day by day), Yahoo's endpoint returns the
whole requested range in a single call, so each run just re-fetches the
last RANGE and merges it into the archive (new data wins on overlapping
dates, in case of late-settled revisions). The merge -- rather than a
flat overwrite -- also means a transient narrower response from Yahoo on
some future day can't quietly shrink the archive.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

YAHOO_URL = 'https://query1.finance.yahoo.com/v8/finance/chart/TTF%3DF'
RANGE = '2y'
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / 'data'
OUT_FILE = DATA_DIR / 'ttf_futures.json'


def fetch_json(attempt=0):
    url = f'{YAHOO_URL}?range={RANGE}&interval=1d'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/120.0 Safari/537.36',
        'Accept': 'application/json',
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        if attempt < 3:
            wait = 2 ** (attempt + 1)
            print(f'  retry {attempt + 1} after {wait}s: {e}', file=sys.stderr)
            time.sleep(wait)
            return fetch_json(attempt + 1)
        raise


def main():
    j = fetch_json()
    result = (j.get('chart') or {}).get('result') or []
    if not result:
        err = (j.get('chart') or {}).get('error')
        sys.exit(f'Yahoo chart API returned no result (error: {err}) -- refusing to write.')

    r = result[0]
    timestamps = r.get('timestamp') or []
    quote = (r.get('indicators') or {}).get('quote', [{}])
    closes = quote[0].get('close') if quote else []
    closes = closes or []

    new_prices = {}
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d')
        new_prices[day] = round(close, 3)

    print(f'Fetched {len(new_prices)} days from Yahoo Finance (TTF=F)')
    if not new_prices:
        sys.exit('No usable close prices parsed -- refusing to touch the archive.')

    existing = {}
    if OUT_FILE.exists():
        try:
            existing = json.loads(OUT_FILE.read_text()).get('prices', {})
        except Exception:
            existing = {}

    merged = {**existing, **new_prices}
    added = len(merged) - len(existing)

    out = {
        'updatedAt': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'symbol': 'TTF=F',
        'currency': r.get('meta', {}).get('currency', 'EUR'),
        'prices': dict(sorted(merged.items())),
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(out, separators=(',', ':')))
    print(f'Wrote {OUT_FILE}: {len(merged)} total days (was {len(existing)}, +{added} new)')


if __name__ == '__main__':
    main()
