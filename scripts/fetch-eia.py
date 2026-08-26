#!/usr/bin/env python3
"""Fetch EIA data and write pre-processed JSON files to data/eia/."""
import json
import os
import sys
import urllib.request
import urllib.parse
from datetime import date, datetime
from pathlib import Path

API_KEY = os.environ.get('EIA_API_KEY', '')
if not API_KEY:
    sys.exit('EIA_API_KEY environment variable is required.')

BASE = 'https://api.eia.gov/v2'

import time

def fetch_page(route, params, offset=0, attempt=0):
    parts = [
        ('api_key', API_KEY),
        ('data[0]', 'value'),
        ('sort[0][column]', 'period'),
        ('sort[0][direction]', 'asc'),
        ('length', '5000'),
        ('offset', str(offset)),
    ]
    for k, v in params.items():
        if isinstance(v, list):
            for item in v:
                parts.append((k, item))
        else:
            parts.append((k, v))
    url = f'{BASE}/{route}/data/?{urllib.parse.urlencode(parts)}'
    try:
        with urllib.request.urlopen(url, timeout=120) as r:
            return json.loads(r.read())
    except Exception as e:
        if attempt < 3:
            wait = 2 ** (attempt + 1)
            print(f'\n  Retry {attempt+1} for {route} (offset={offset}) after {wait}s: {e}')
            time.sleep(wait)
            return fetch_page(route, params, offset, attempt + 1)
        raise

def fetch_all(route, params):
    all_rows = []
    offset = 0
    while True:
        data = fetch_page(route, params, offset)
        rows = data.get('response', {}).get('data', [])
        all_rows.extend(rows)
        print(f'  {route}: {len(all_rows)} rows', end='\r', flush=True)
        if len(rows) < 5000:
            break
        offset += 5000
    print(f'  {route}: {len(all_rows)} rows total')
    return all_rows

def to_series_map(rows):
    out = {}
    for row in rows:
        sid = row.get('series', '')
        try:
            v = float(row['value'])
        except (KeyError, TypeError, ValueError):
            continue
        out.setdefault(sid, []).append({'date': row['period'], 'value': v})
    for arr in out.values():
        arr.sort(key=lambda x: x['date'])
    return out

def years_ago(n, monthly=False):
    d = date.today().replace(year=date.today().year - n)
    return d.isoformat()[:7] if monthly else d.isoformat()

def now_iso():
    return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

def write(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, separators=(',', ':')))
    print(f'  Wrote {path} ({p.stat().st_size:,} bytes)')


# ── Inventory ─────────────────────────────────────────────────────────────────
print('=== Inventory ===')
inv_rows = fetch_all('petroleum/stoc/wstk', {
    'frequency': 'weekly',
    'start': years_ago(15),
    'facets[series][]': ['WCRSTUS1', 'WCESTUS1', 'WCSSTUS1', 'WGRSTUS1', 'WDISTUS1', 'WKJSTUS1'],
})
write('data/eia/inventory.json', {
    'updatedAt': now_iso(),
    'series': to_series_map(inv_rows),
})

# ── Petroleum Flows ───────────────────────────────────────────────────────────
# Fetch each product group separately (~4 series × 20yr × 52wk ≈ 4,000 rows each)
# to avoid pagination timeouts on a single large request.
print('=== Petroleum Flows ===')
flow_groups = [
    ['WCRFPUS2', 'WCRIMUS2', 'WCREXUS2', 'WCRRIUS2'],   # crude
    ['WGFRPUS2', 'WGFIMUS2', 'WGFEXUS2', 'WGFUPUS2'],   # gasoline
    ['WDIRPUS2', 'WDIIMUS2', 'WDIEXUS2', 'WDIUPUS2'],   # distillate
    ['WKJRPUS2', 'WKJIMUS2', 'WKJEXUS2', 'WKJUPUS2'],   # jet fuel
]
pet_map = {}
for group in flow_groups:
    rows = fetch_all('petroleum/sum/sndw', {
        'frequency': 'weekly',
        'start': years_ago(20),
        'facets[series][]': group,
    })
    pet_map.update(to_series_map(rows))

# ── LNG Flows ─────────────────────────────────────────────────────────────────
print('=== LNG Flows ===')
lng_rows = fetch_all('natural-gas/move/expc', {
    'frequency': 'monthly',
    'start': years_ago(22, monthly=True),
    'facets[process][]': 'ENG',
})
today_mon = date.today().strftime('%Y-%m')
lng_by_period = {}
for row in lng_rows:
    period = row.get('period', '')
    if period > today_mon:
        continue
    try:
        v = float(row['value'])
    except (KeyError, TypeError, ValueError):
        continue
    lng_by_period[period] = lng_by_period.get(period, 0) + v

lng_agg = sorted(
    [{'date': k, 'value': v} for k, v in lng_by_period.items()],
    key=lambda x: x['date']
)
write('data/eia/flows.json', {
    'updatedAt': now_iso(),
    'petroleum': pet_map,
    'lng': lng_agg,
})

# ── Spot price series discovery (one-time diagnostic) ─────────────────────────
print('=== Spot Price Series Discovery ===')
try:
    disc_url = f'{BASE}/petroleum/pri/spt/facet/series/?api_key={API_KEY}'
    with urllib.request.urlopen(disc_url, timeout=30) as r:
        disc = json.loads(r.read())
    all_series = disc.get('response', {}).get('facets', {}).get('series', [])
    print(f'  Total series in petroleum/pri/spt: {len(all_series)}')
    for s in all_series:
        sid = s.get('id', '')
        name = s.get('alias') or s.get('name') or ''
        kws = ['gas', 'petrol', 'motor', 'rbob', 'y35ny', 'rgc']
        if any(k in sid.lower() or k in name.lower() for k in kws):
            print(f'  SERIES: {sid!r} => {name!r}')
except Exception as e:
    print(f'  Discovery failed: {e}')

# ── Prices ────────────────────────────────────────────────────────────────────
print('=== Prices ===')
crude_rows = fetch_all('petroleum/pri/spt', {
    'frequency': 'weekly', 'start': years_ago(22),
    'facets[series][]': [
        'RWTC', 'RBRTE',
        'EER_EPM0F_PF4_Y35NY_DPG',   # NY Harbor RBOB gasoline spot ($/gal)
        'EER_EPD2F_PF4_Y35NY_DPG',   # NY Harbor No.2 heating oil spot ($/gal)
    ],
})
fuel_rows = fetch_all('petroleum/pri/gnd', {
    'frequency': 'weekly', 'start': years_ago(22),
    'facets[series][]': ['EMM_EPM0_PTE_NUS_DPG', 'EMD_EPD2D_PTE_NUS_DPG'],
})
ng_rows = fetch_all('natural-gas/pri/fut', {
    'frequency': 'weekly', 'start': years_ago(22),
    'facets[series][]': 'RNGWHHD',
    'facets[process][]': 'PS0',
})
lng_price_rows = fetch_all('natural-gas/move/expc', {
    'frequency': 'monthly', 'start': years_ago(22, monthly=True),
    'facets[process][]': 'PNG',
})
lng_price_by_period = {}
for row in lng_price_rows:
    period = row.get('period', '')
    if period > today_mon:
        continue
    try:
        v = float(row['value'])
    except (KeyError, TypeError, ValueError):
        continue
    lng_price_by_period[period] = lng_price_by_period.get(period, 0) + v

lng_price_agg = sorted(
    [{'date': k, 'value': v} for k, v in lng_price_by_period.items()],
    key=lambda x: x['date']
)
prices = {}
prices.update(to_series_map(crude_rows))
prices.update(to_series_map(fuel_rows))
prices.update(to_series_map(ng_rows))
prices['lng_price'] = lng_price_agg
write('data/eia/prices.json', {'updatedAt': now_iso(), **prices})

print('Done.')
