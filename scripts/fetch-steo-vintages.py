#!/usr/bin/env python3
"""Fetch earlier releases (vintages) of the EIA Short-Term Energy Outlook.

The EIA API only serves the current STEO release, so earlier releases are read
from EIA's archive of the full STEO workbook
(https://www.eia.gov/outlooks/steo/archives/<mon><yy>_base.xlsx). Tables 3a and
3c in the workbook hold the same series as scripts/fetch-steo.py fetches from
the API, monthly, for the current year minus four through the forecast
horizon. Quarterly and annual values are derived the way the API derives them
(day-weighted averages; inventory levels at the end of the period), so a
vintage lines up exactly with data/eia/steo.json for the same release.

Writes data/eia/steo_vintages.json:
  {"updatedAt": ..., "vintages": [{"release": "2026-10", "lastHistMonth":
   "2026-09", "data": {"monthly": {"PAPR_WORLD": ["2022-01", [v, v, ...]], ...},
   "quarterly": {...}, "annual": {...}}}, ...]}  (newest first)

Each series is stored compactly as [first period, consecutive values] (null for
a gap); expanded it is the same {period: value} map as in steo.json.

Releases already in the file are kept as they are; only missing ones within
the window are downloaded. A release that is not published yet (404) is
skipped and tried again on the next run.
"""
import calendar
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

try:
    import openpyxl
except ImportError:
    sys.exit('openpyxl is required (pip install openpyxl).')

OUT = Path('data/eia/steo_vintages.json')
ARCHIVE = 'https://www.eia.gov/outlooks/steo/archives/{}_base.xlsx'
MONTHS_BACK = int(os.environ.get('STEO_VINTAGE_MONTHS', '24'))
MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

# Same series as scripts/fetch-steo.py (Tables 3a and 3c).
SERIES = {
    'PAPR_WORLD', 'COPR_WORLD', 'WORLD_NC', 'PAPR_OPEC', 'COPR_OPEC', 'OPEC_NC',
    'PAPR_NONOPEC', 'COPR_NONOPEC', 'NONOPEC_NC',
    'PATC_WORLD', 'PATC_OECD', 'PATC_CA', 'PATC_OECD_EUROPE', 'PATC_JA',
    'PATC_US', 'PATC_UST', 'PATC_OTHER_OECD',
    'PATC_NON_OECD', 'PATC_CH', 'PATC_FSU', 'PATC_NONOECD_EUROPE',
    'PATC_OTHER_ASIA', 'PATC_OTHER_NONOECD',
    'T3_STCHANGE_WORLD', 'T3_STCHANGE_US', 'T3_STCHANGE_OOECD', 'T3_STCHANGE_NOECD',
    'PASC_OECD_T3', 'PASC_US', 'PASC_OOECD_T3',
    'PAPR_OPECPLUS', 'PAPR_US', 'PAPR_NONOPECPLUS_XUS',
    'PAPR_AG', 'PAPR_CF', 'PAPR_EK', 'PAPR_GB', 'PAPR_IR', 'PAPR_IZ', 'PAPR_KU',
    'PAPR_LY', 'PAPR_NI', 'PAPR_SA', 'PAPR_VE',
    'PAPR_OPECPLUS_OPEC', 'PAPR_OPECPLUS_OTHER',
    'PAPR_AJ', 'PAPR_BA', 'PAPR_BX', 'PAPR_KZ', 'PAPR_MY', 'PAPR_MX', 'PAPR_MU',
    'PAPR_RS', 'PAPR_OD', 'PAPR_SU',
}


def download(tag, attempt=0):
    req = urllib.request.Request(ARCHIVE.format(tag), headers={'User-Agent': 'Mozilla/5.0 (steo-archive)'})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        err = e
    except Exception as e:  # noqa: BLE001
        err = e
    if attempt < 3:
        time.sleep(2 ** (attempt + 1))
        return download(tag, attempt + 1)
    print(f'  {tag}: download failed ({err})')
    return None


def parse(blob):
    """Monthly values per series from Tables 3a and 3c of a STEO workbook."""
    wb = openpyxl.load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    monthly = {}
    for sheet in ('3atab', '3ctab'):
        if sheet not in wb.sheetnames:
            continue
        rows = list(wb[sheet].iter_rows(values_only=True))
        # Row 3: year at the first month of each year; row 4: month names.
        periods, year = {}, None
        for j, (y, m) in enumerate(zip(rows[2], rows[3])):
            if isinstance(y, (int, float)):
                year = int(y)
            if year and m in MON:
                periods[j] = f'{year}-{MON.index(m) + 1:02d}'
        for r in rows[4:]:
            sid = r[0].strip().upper() if isinstance(r[0], str) else None
            if sid not in SERIES or sid in monthly:
                continue
            s = {p: round(float(r[j]), 3) for j, p in periods.items()
                 if j < len(r) and isinstance(r[j], (int, float))}
            if s:
                monthly[sid] = s
    return monthly


def aggregate(series, sid, kind):
    """Quarterly ('q') or annual ('a') values as the EIA API reports them."""
    groups = {}
    for p, v in series.items():
        y, m = p[:4], int(p[5:7])
        groups.setdefault(y if kind == 'a' else f'{y}-Q{(m - 1) // 3 + 1}', []).append((p, v))
    out = {}
    for k, items in groups.items():
        if len(items) != (12 if kind == 'a' else 3):
            continue
        items.sort()
        if sid.startswith('PASC_'):          # inventory level: end of period
            out[k] = items[-1][1]
        else:                                # rates: day-weighted average
            w = [calendar.monthrange(int(p[:4]), int(p[5:7]))[1] for p, _ in items]
            out[k] = round(sum(v * x for (_, v), x in zip(items, w)) / sum(w), 3)
    return out


def nxt(p):
    if len(p) == 4:
        return str(int(p) + 1)
    y = int(p[:4])
    if p[5] == 'Q':
        q = int(p[-1])
        return f'{y + 1}-Q1' if q == 4 else f'{y}-Q{q + 1}'
    m = int(p[5:7])
    return f'{y + 1}-01' if m == 12 else f'{y}-{m + 1:02d}'


def compact(series):
    keys = sorted(series)
    vals, p = [], keys[0]
    while p <= keys[-1]:
        vals.append(series.get(p))
        p = nxt(p)
    return [keys[0], vals]


def expand(c):
    out, p = {}, c[0]
    for v in c[1]:
        if v is not None:
            out[p] = v
        p = nxt(p)
    return out


def vintage(release, monthly):
    # Same historical/forecast break as fetch-steo.py: the OPEC member-country
    # series are published for historical/estimated months only.
    last_hist = max([max(monthly.get(s) or {'0000-00': 0}) for s in ('PAPR_SA', 'PAPR_IZ')])
    return {
        'release': release,
        'lastHistMonth': last_hist,
        'data': {
            'monthly': {s: compact(v) for s, v in monthly.items()},
            'quarterly': {s: compact(a) for s, v in monthly.items() if (a := aggregate(v, s, 'q'))},
            'annual': {s: compact(a) for s, v in monthly.items() if (a := aggregate(v, s, 'a'))},
        },
    }


def releases(today, back):
    y, m = today.year, today.month
    for _ in range(back + 1):
        yield f'{y}-{m:02d}', f'{MON[m - 1].lower()}{y % 100:02d}'
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)


old = {}
if OUT.exists():
    try:
        old = {v['release']: v for v in json.loads(OUT.read_text()).get('vintages', [])
               if isinstance(next(iter(v['data']['monthly'].values())), list)}
    except Exception:  # noqa: BLE001
        old = {}

keep, changed = {}, False
for release, tag in releases(date.today(), MONTHS_BACK):
    if release in old:
        keep[release] = old[release]
        continue
    blob = download(tag)
    if not blob:
        print(f'  {tag}: not available')
        continue
    monthly = parse(blob)
    if 'PAPR_WORLD' not in monthly or 'PATC_WORLD' not in monthly:
        print(f'  {tag}: no Table 3a data in workbook, skipped')
        continue
    keep[release] = vintage(release, monthly)
    changed = True
    print(f'  {tag}: {len(monthly)} series, history through {keep[release]["lastHistMonth"]}')

if set(keep) != set(old):
    changed = True
if not keep:
    sys.exit('No STEO vintages available — keeping existing file.')
if not changed:
    print('  No new STEO vintages')
    sys.exit(0)

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps({
    'updatedAt': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    'source': 'U.S. Energy Information Administration (EIA), Short-Term Energy Outlook archives, Tables 3a and 3c',
    'vintages': [keep[r] for r in sorted(keep, reverse=True)],
}, separators=(',', ':')))
print(f'  Wrote {OUT} ({OUT.stat().st_size:,} bytes, {len(keep)} releases)')
