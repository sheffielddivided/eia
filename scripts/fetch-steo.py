#!/usr/bin/env python3
"""Fetch EIA Short-Term Energy Outlook (STEO) world oil balance data.

Covers the series behind STEO Table 3a (world production, consumption and
inventories) and Table 3c (production by OPEC / OPEC+ / country), and writes
them to data/eia/steo.json in monthly, quarterly and annual resolution.
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

API_KEY = os.environ.get('EIA_API_KEY', '')
if not API_KEY:
    sys.exit('EIA_API_KEY environment variable is required.')

BASE = 'https://api.eia.gov/v2/steo/data/'
OUT = Path('data/eia/steo.json')

# Table 3a
T3A = [
    'PAPR_WORLD', 'COPR_WORLD', 'WORLD_NC',
    'PAPR_OPEC', 'COPR_OPEC', 'OPEC_NC',
    'PAPR_NONOPEC', 'COPR_NONOPEC', 'NONOPEC_NC',
    'PATC_WORLD', 'PATC_OECD', 'PATC_CA', 'PATC_OECD_EUROPE', 'PATC_JA',
    'PATC_US', 'PATC_UST', 'PATC_OTHER_OECD',
    'PATC_NON_OECD', 'PATC_CH', 'PATC_FSU', 'PATC_NONOECD_EUROPE',
    'PATC_OTHER_ASIA', 'PATC_OTHER_NONOECD',
    'T3_STCHANGE_WORLD', 'T3_STCHANGE_US', 'T3_STCHANGE_OOECD', 'T3_STCHANGE_NOECD',
    'PASC_OECD_T3', 'PASC_US', 'PASC_OOECD_T3',
]
# Table 3c
T3C = [
    'PAPR_OPECPLUS', 'PAPR_US', 'PAPR_NONOPECPLUS_XUS',
    'PAPR_AG', 'PAPR_CF', 'PAPR_EK', 'PAPR_GB', 'PAPR_IR', 'PAPR_IZ', 'PAPR_KU',
    'PAPR_LY', 'PAPR_NI', 'PAPR_SA', 'PAPR_VE',
    'PAPR_OPECPLUS_OPEC', 'PAPR_OPECPLUS_OTHER',
    'PAPR_AJ', 'PAPR_BA', 'PAPR_BX', 'PAPR_KZ', 'PAPR_MY', 'PAPR_MX', 'PAPR_MU',
    'PAPR_RS', 'PAPR_OD', 'PAPR_SU',
]
SERIES = T3A + T3C

# Full history: most series start in 1993 (consumption 1990, OECD stocks 2003).
FREQS = {
    'monthly':   '1990-01',
    'quarterly': '1990-Q1',
    'annual':    '1990',
}


def fetch(freq, start, attempt=0):
    parts = [
        ('api_key', API_KEY),
        ('frequency', freq),
        ('data[0]', 'value'),
        ('start', start),
        ('sort[0][column]', 'period'),
        ('sort[0][direction]', 'asc'),
        ('length', '5000'),
    ] + [('facets[seriesId][]', s) for s in SERIES]
    rows, offset = [], 0
    while True:
        url = BASE + '?' + urllib.parse.urlencode(parts + [('offset', str(offset))])
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                page = json.loads(r.read())['response']
        except Exception as e:
            if attempt < 3:
                wait = 2 ** (attempt + 1)
                print(f'  Retry {attempt + 1} ({freq}) after {wait}s: {e}')
                time.sleep(wait)
                return fetch(freq, start, attempt + 1)
            raise
        batch = page.get('data', [])
        rows.extend(batch)
        if len(batch) < 5000:
            break
        offset += 5000
    print(f'  steo {freq}: {len(rows)} rows')
    return rows


meta, data = {}, {}
for freq, start in FREQS.items():
    out = {}
    for row in fetch(freq, start):
        sid = row.get('seriesId')
        try:
            v = round(float(row['value']), 3)
        except (KeyError, TypeError, ValueError):
            continue
        out.setdefault(sid, {})[row['period']] = v
        meta.setdefault(sid, {'name': row.get('seriesDescription', ''), 'unit': row.get('unit', '')})
    data[freq] = out

missing = [s for s in SERIES if s not in data['monthly']]
if missing:
    print(f'  WARNING: no data for {missing}')
if 'PAPR_WORLD' not in data['monthly']:
    sys.exit('No STEO data returned — keeping existing file.')

# Historical/forecast break. The API carries no flag for it, but the OPEC
# member-country series (Table 3c) are published only for historical/estimated
# months, so their last period marks where the pure model forecast starts.
last_hist = max(max(data['monthly'].get('PAPR_SA', {'0000-00': 0})),
                max(data['monthly'].get('PAPR_IZ', {'0000-00': 0})))

# Keep the stored release label (and updatedAt) unless the numbers changed, so
# the nightly job doesn't create commits between monthly STEO releases.
release = date.today().strftime('%Y-%m')
updated = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
if OUT.exists():
    try:
        old = json.loads(OUT.read_text())
        if old.get('data') == data:
            release, updated = old.get('release', release), old.get('updatedAt', updated)
    except Exception:
        pass

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps({
    'updatedAt': updated,
    'release': release,
    'lastHistMonth': last_hist,
    'meta': meta,
    'data': data,
}, separators=(',', ':')))
print(f'  Wrote {OUT} ({OUT.stat().st_size:,} bytes)')
