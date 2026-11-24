#!/usr/bin/env python3
"""Fetch European natural-gas consumption balances from Eurostat and write
data/eurostat/gas_consumption.json for gasforbruk.html to read statically.

Two Eurostat datasets (open JSON-stat API, no key needed):

  nrg_cb_gas   Annual complete gas balance, 1990 -> last full year.
               Full sector split: power & heat plants, industry by branch,
               non-energy use (feedstock), households, services, transport ...
  nrg_cb_gasm  Monthly gas balance (MOS GAS), 2008 -> ~2 months ago.
               Inland consumption + input to main-activity power/heat plants.
               (Eurostat's monthly industry / other-sector split is reported
               by only a handful of countries since 2023, so it is not used.)

Values are stored raw per Eurostat balance code (nrg_bal); the page derives
the consumption components itself. Units: TJ (GCV) and million m3.
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = 'https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data'

EU27 = ['BE', 'BG', 'CZ', 'DK', 'DE', 'EE', 'IE', 'EL', 'ES', 'FR', 'HR', 'IT',
        'CY', 'LV', 'LT', 'LU', 'HU', 'MT', 'NL', 'AT', 'PL', 'PT', 'RO', 'SI',
        'SK', 'FI', 'SE']
GEOS = ['EU27_2020'] + EU27 + ['NO']

UNITS = ['TJ_GCV', 'MIO_M3']

ANNUAL_BAL = [
    # totals
    'IC_CAL',
    # transformation (power & heat split by plant type)
    'TI_E', 'TI_EHG_MAPE_E', 'TI_EHG_MAPCHP_E', 'TI_EHG_MAPH_E',
    'TI_EHG_APE_E', 'TI_EHG_APCHP_E', 'TI_EHG_APH_E',
    # energy sector own use, losses, stat diff
    'NRG_E', 'TRANSL_DL', 'DL', 'STATDIFF',
    # final consumption
    'FC_NE',
    'FC_IND_E', 'FC_IND_IS_E', 'FC_IND_CPC_E', 'FC_IND_NFM_E', 'FC_IND_NMM_E',
    'FC_IND_TE_E', 'FC_IND_MAC_E', 'FC_IND_MQ_E', 'FC_IND_FBT_E', 'FC_IND_PPP_E',
    'FC_IND_WP_E', 'FC_IND_CON_E', 'FC_IND_TL_E', 'FC_IND_NSP_E',
    'FC_TRA_E', 'FC_TRA_ROAD_E', 'FC_TRA_PIPE_E',
    'FC_OTH_E', 'FC_OTH_HH_E', 'FC_OTH_CP_E', 'FC_OTH_AF_E', 'FC_OTH_FISH_E',
    'FC_OTH_NSP_E',
]

MONTHLY_BAL = ['IC_CAL_MG', 'TI_EHG_MAP']

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'data' / 'eurostat' / 'gas_consumption.json'


def fetch(dataset, params, attempt=0):
    q = [('format', 'JSON'), ('lang', 'en'), ('siec', 'G3000')] + params
    url = f'{API}/{dataset}?{urllib.parse.urlencode(q)}'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; gasforbruk-data-fetch/1.0)',
        'Accept': 'application/json',
    })
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        if attempt < 4:
            wait = 5 * 2 ** attempt
            print(f'  retry {attempt + 1} after {wait}s: {e}', file=sys.stderr)
            time.sleep(wait)
            return fetch(dataset, params, attempt + 1)
        raise


def unpack(js):
    """JSON-stat 2.0 -> {geo: {unit: {bal: {time: value}}}} plus time list."""
    ids, size = js['id'], js['size']
    cats = {k: js['dimension'][k]['category']['index'] for k in ids}
    inv = {k: {v: c for c, v in cats[k].items()} for k in ids}
    strides = [1] * len(ids)
    for i in range(len(ids) - 2, -1, -1):
        strides[i] = strides[i + 1] * size[i + 1]
    out = {}
    for flat, val in js['value'].items():
        flat = int(flat)
        coord = {}
        for i, k in enumerate(ids):
            coord[k] = inv[k][(flat // strides[i]) % size[i]]
        if val is None:
            continue
        (out.setdefault(coord['geo'], {})
            .setdefault(coord['unit'], {})
            .setdefault(coord['nrg_bal'], {}))[coord['time']] = round(float(val)) if coord['unit'] == 'TJ_GCV' else round(float(val), 1)
    times = sorted(cats['time'], key=cats['time'].get)
    return out, times, js.get('updated')


def build(dataset, bals):
    print(f'[{dataset}] fetching {len(GEOS)} geos x {len(bals)} balance items ...')
    merged, all_times, updated = {}, set(), None
    # one request per geo keeps responses small and failures isolated
    for geo in GEOS:
        params = [('geo', geo)] + [('unit', u) for u in UNITS] + [('nrg_bal', b) for b in bals]
        try:
            js = fetch(dataset, params)
        except Exception as e:  # keep going; a missing country is not fatal
            print(f'  {geo}: FAILED {e}', file=sys.stderr)
            continue
        data, times, upd = unpack(js)
        merged.update(data)
        all_times.update(times)
        updated = max(filter(None, [updated, upd])) if upd else updated
        n = sum(len(s) for u in data.get(geo, {}).values() for s in u.values())
        print(f'  {geo}: {n} values, {times[0] if times else "-"} .. {times[-1] if times else "-"}')
    if 'EU27_2020' not in merged:
        raise SystemExit(f'{dataset}: EU27 aggregate missing - aborting without writing')
    return {'eurostatUpdated': updated, 'periods': sorted(all_times), 'geo': merged}


def slim_units(block, keep_m3):
    """Million m3 is only needed for the TJ->bcm conversion factor."""
    for units in block['geo'].values():
        m3 = units.get('MIO_M3', {})
        units['MIO_M3'] = {b: v for b, v in m3.items() if b in keep_m3}


def eu_monthly_from_countries(monthly):
    """Eurostat's monthly EU27 aggregate has holes (e.g. power-plant input is
    missing for all of 2019 and under-covers 2010-2016). Rebuild it as the sum
    of the 27 member states, keeping a month only when enough countries
    report. Cyprus and Malta use little or no pipeline gas, so 25 of 27 is
    treated as complete."""
    geo = monthly['geo']
    eu, cover = {'TJ_GCV': {}, 'MIO_M3': {}}, {}
    for unit, bals in (('TJ_GCV', MONTHLY_BAL), ('MIO_M3', ['IC_CAL_MG'])):
        for bal in bals:
            out, n = {}, {}
            for t in monthly['periods']:
                vals = [geo[c][unit][bal][t] for c in EU27
                        if t in geo.get(c, {}).get(unit, {}).get(bal, {})]
                n[t] = len(vals)
                if len(vals) >= 25:
                    out[t] = round(sum(vals), 1)
            eu[unit][bal] = out
            if unit == 'TJ_GCV':
                cover[bal] = n
    geo['EU27_2020'] = eu
    monthly['euReporters'] = cover


def main():
    annual = build('nrg_cb_gas', ANNUAL_BAL)
    monthly = build('nrg_cb_gasm', MONTHLY_BAL)
    slim_units(annual, {'IC_CAL'})
    slim_units(monthly, {'IC_CAL_MG'})
    eu_monthly_from_countries(monthly)
    doc = {
        'updatedAt': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'source': 'Eurostat nrg_cb_gas (annual) and nrg_cb_gasm (monthly)',
        'geos': GEOS,
        'annual': annual,
        'monthly': monthly,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, separators=(',', ':')))
    print(f'Wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} kB)')


if __name__ == '__main__':
    main()
