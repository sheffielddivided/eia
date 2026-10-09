#!/usr/bin/env python3
"""Fetch AGSI (storage) and ALSI (LNG) data and write pre-processed JSON
files to data/agsi.json and data/alsi.json for eugas.html to read statically.

Runs once daily via GitHub Actions instead of eugas.html hitting the APIs
live from the browser on every page load.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

API_KEY = os.environ.get('AGSI_API_KEY', 'eb259b94628dbfddbaff45e4b3f772a2')

AGSI_URL = 'https://agsi.gie.eu/api'
ALSI_URL = 'https://alsi.gie.eu/api'

CUR_YEAR = date.today().year
HIST_YEARS = [CUR_YEAR - n for n in range(5, 0, -1)]  # 5 years back .. 1 year back
ALL_YEARS = HIST_YEARS + [CUR_YEAR]

AGSI_SELECTIONS = {
    'eu': {'type': 'eu'},
    'de': {'country': 'DE'},
    'it': {'country': 'IT'},
    'nl': {'country': 'NL'},
    'fr': {'country': 'FR'},
}

ALSI_SELECTIONS = {
    'eu': {'type': 'eu'},
    'es': {'country': 'ES'},
    'fr': {'country': 'FR'},
    'it': {'country': 'IT'},
    'nl': {'country': 'NL'},
}

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / 'data'


def fetch_json(base_url, params, attempt=0):
    url = f'{base_url}?{urllib.parse.urlencode(params)}'
    req = urllib.request.Request(url, headers={
        'x-key': API_KEY,
        'User-Agent': 'Mozilla/5.0 (compatible; eugas-data-fetch/1.0)',
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
            return fetch_json(base_url, params, attempt + 1)
        raise


def fetch_year_records(base_url, sel, year, _retry=0):
    """Fetch all records for a year, paginating as needed.

    The API caps each page at 300 records regardless of the requested
    'size', and paginates via 'page' (1-indexed) with 'last_page' in the
    response telling us how many pages exist in total.

    The API has also been observed to occasionally return a suspiciously
    small or empty 'data' array for an otherwise-valid query (HTTP 200,
    no error) — likely soft rate-limiting under the rapid-fire request
    pattern this script uses. If the final count looks implausibly low for
    the requested range, retry the whole year a couple of times before
    accepting it (and a small delay between paged requests reduces how
    often this happens in the first place).
    """
    from_ = f'{year}-01-01'
    today = date.today()
    to = today.isoformat() if year == CUR_YEAR else f'{year}-12-31'
    all_records = []
    page = 1
    while True:
        params = {'from': from_, 'to': to, 'size': '300', 'page': str(page)}
        params.update(sel)
        j = fetch_json(base_url, params)
        all_records.extend(j.get('data') or [])
        last_page = j.get('last_page', 1)
        if page >= last_page:
            break
        page += 1
        time.sleep(0.3)

    expected_min = max(0, (today - date(year, 1, 1)).days - 10) if year == CUR_YEAR else 300
    if len(all_records) < expected_min and _retry < 2:
        print(f'  suspiciously few records ({len(all_records)} < {expected_min}) for {year}, retrying...', file=sys.stderr)
        time.sleep(2 ** (_retry + 1))
        return fetch_year_records(base_url, sel, year, _retry=_retry + 1)

    return all_records


def day_map(records, field_candidates, capacity_field=None, capacity_margin=1.5):
    """{'MM-DD': float} for the first non-null field in field_candidates per record.

    If capacity_field is given, a value exceeding capacity_margin times that
    record's capacity_field is treated as a bad reading and the day is left
    out entirely (rather than falling back to a later candidate field, which
    could substitute a capacity figure for an actual flow value). GIE's feed
    has been observed to occasionally report a single-day spike far above
    the physically possible maximum (seen: ALSI EU sendOut at ~13x that
    day's declared dtrs capacity) — a real flow can't exceed its own
    declared capacity, so this is read as a data error, not a real event.
    """
    out = {}
    for r in records:
        date_str = r.get('gasDayStart') or r.get('gas_day') or r.get('date') or ''
        if not date_str:
            continue
        mmdd = date_str[5:10]
        cap_val = None
        if capacity_field:
            cap = r.get(capacity_field)
            try:
                cap_val = float(cap) if cap is not None else None
            except (TypeError, ValueError):
                cap_val = None
        for field in field_candidates:
            v = r.get(field)
            if v is None:
                continue
            try:
                val = float(v)
            except (TypeError, ValueError):
                continue
            if cap_val and cap_val > 0 and val > cap_val * capacity_margin:
                break  # implausible vs. declared capacity — leave day missing
            out[mmdd] = val
            break
    return out


def build_agsi():
    selections = {}
    for key, sel in AGSI_SELECTIONS.items():
        print(f'[AGSI] {key} ...')
        full, withdrawal, injection = {}, {}, {}
        for year in ALL_YEARS:
            records = fetch_year_records(AGSI_URL, sel, year)
            full[str(year)] = day_map(records, ['full'])
            withdrawal[str(year)] = day_map(records, ['withdrawal'], capacity_field='withdrawalCapacity')
            injection[str(year)] = day_map(records, ['injection'], capacity_field='injectionCapacity')
            print(f'  {year}: {len(records)} records')
        selections[key] = {'full': full, 'withdrawal': withdrawal, 'injection': injection}
    return selections


def build_alsi():
    selections = {}
    for key, sel in ALSI_SELECTIONS.items():
        print(f'[ALSI] {key} ...')
        send_out = {}
        for year in ALL_YEARS:
            records = fetch_year_records(ALSI_URL, sel, year)
            send_out[str(year)] = day_map(records, ['sendOut', 'dtrs', 'dtrsActual', 'send'], capacity_field='dtrs')
            print(f'  {year}: {len(records)} records')
        selections[key] = {'sendOut': send_out}
    return selections


def main():
    updated_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    agsi = {'updatedAt': updated_at, 'curYear': CUR_YEAR, 'histYears': HIST_YEARS,
            'selections': build_agsi()}
    alsi = {'updatedAt': updated_at, 'curYear': CUR_YEAR, 'histYears': HIST_YEARS,
            'selections': build_alsi()}

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / 'agsi.json').write_text(json.dumps(agsi, separators=(',', ':')))
    (DATA_DIR / 'alsi.json').write_text(json.dumps(alsi, separators=(',', ':')))
    print('Wrote data/agsi.json and data/alsi.json')


if __name__ == '__main__':
    main()
