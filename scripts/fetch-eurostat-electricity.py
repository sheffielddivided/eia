#!/usr/bin/env python3
"""Fetch European electricity generation by energy source from Eurostat and
write data/eurostat/electricity.json for elektrisitet.html.

  nrg_bal_peh  Annual gross electricity production by fuel, 1990 -> ~1.5 yrs ago
  nrg_cb_pem   Monthly net electricity generation by fuel, ~2 months lag.
               A full fuel split exists from 2017; earlier months only split
               combustible fuels as one block, so the monthly series starts 2017.

Sources are grouped here into a fixed set of categories (GWh); "other" is the
remainder against the reported total (pumped hydro, geothermal, non-renewable
waste, other fuels ...). The monthly EU27 series is the sum of member states,
because Eurostat's own monthly aggregate is incomplete.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from eurostat_common import EU27, fetch, periods, records

GEOS = ['EU27_2020'] + EU27 + ['NO']

# category -> list of (siec, sign)
ANNUAL = {
    'nuclear': [('N900H', 1)],
    'coal':    [('C0000X0350-0370', 1), ('C0350-0370', 1), ('P1000', 1), ('S2000', 1)],
    'gas':     [('G3000', 1)],
    'oil':     [('O4000XBIO', 1)],
    'hydro':   [('RA100', 1), ('RA130', -1)],
    'wind':    [('RA300', 1)],
    'solar':   [('RA410', 1), ('RA420', 1)],
    # bioenergy: solid biofuels, biogases, renewable waste, liquid biofuels
    'bio':     [('R5110-5150_W6000RI', 1), ('R5300', 1), ('W6210', 1),
                ('R5210P', 1), ('R5220P', 1), ('R5290', 1)],
}
MONTHLY = {
    'nuclear': [('N9000', 1)],
    'coal':    [('C0000', 1)],
    'gas':     [('G3000', 1)],
    'oil':     [('O4000XBIO', 1)],
    'hydro':   [('RA100', 1), ('RA130', -1)],
    'wind':    [('RA300', 1)],
    'solar':   [('RA400', 1)],
    'bio':     [('CF_R', 1)],
    # Some countries (Italy until 2021, Estonia) report combustible fuels
    # only as one block: CF minus its reported parts, kept as its own category.
    'thermal': [('CF', 1), ('C0000', -1), ('G3000', -1), ('O4000XBIO', -1),
                ('CF_R', -1), ('CF_NR_OTH', -1)],
}
MONTHLY_FROM = '2017-01'

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'data' / 'eurostat' / 'electricity.json'


def group(raw, groups, period_list):
    """raw: {siec: {period: GWh}} -> {cat: {period: GWh}} incl. total/other."""
    out = {c: {} for c in list(groups) + ['other', 'total']}
    for p in period_list:
        tot = raw.get('TOTAL', {}).get(p)
        if tot is None:
            continue
        s = 0.0
        for cat, parts in groups.items():
            v = sum(sign * raw.get(siec, {}).get(p, 0.0) for siec, sign in parts)
            v = round(max(v, 0.0), 1)
            if cat == 'thermal' and v < 0.01 * tot:
                v = 0.0  # rounding noise, not a real gap
            out[cat][p] = v
            s += out[cat][p]
        out['total'][p] = round(tot, 1)
        out['other'][p] = round(tot - s, 1)
    return out


def build(dataset, groups, extra_params, keep):
    sieces = sorted({s for parts in groups.values() for s, _ in parts} | {'TOTAL'})
    print(f'[{dataset}] {len(GEOS)} geos x {len(sieces)} fuels ...')
    out, all_p, updated = {}, set(), None
    for geo in GEOS:
        try:
            js = fetch(dataset, [('geo', geo), ('unit', 'GWH')] + extra_params + [('siec', s) for s in sieces])
        except Exception as e:
            print(f'  {geo}: FAILED {e}', file=sys.stderr)
            continue
        raw = {}
        for co, v in records(js):
            if keep(co['time']):
                raw.setdefault(co['siec'], {})[co['time']] = v
        ps = [p for p in periods(js) if keep(p)]
        all_p.update(ps)
        out[geo] = group(raw, groups, ps)
        updated = max(filter(None, [updated, js.get('updated')]))
        n = len(out[geo]['total'])
        print(f'  {geo}: {n} periods')
    return out, sorted(all_p), updated


def eu_sum(geo_data, plist):
    """Sum member states for each month where >= 25 of 27 report a total."""
    eu = {c: {} for c in list(MONTHLY) + ['other', 'total']}
    cover = {}
    for p in plist:
        rep = [c for c in EU27 if p in geo_data.get(c, {}).get('total', {})]
        cover[p] = len(rep)
        if len(rep) < 25:
            continue
        for cat in eu:
            eu[cat][p] = round(sum(geo_data[c][cat].get(p, 0.0) for c in rep), 1)
    return eu, cover


def main():
    annual, ap, aupd = build('nrg_bal_peh', ANNUAL, [('nrg_bal', 'GEP')], lambda t: True)
    monthly, mp, mupd = build('nrg_cb_pem', MONTHLY, [], lambda t: t >= MONTHLY_FROM)
    if 'EU27_2020' not in annual:
        raise SystemExit('EU27 annual missing - aborting without writing')
    monthly['EU27_2020'], cover = eu_sum(monthly, mp)
    doc = {
        'updatedAt': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'unit': 'GWh',
        'geos': GEOS,
        'annual': {'eurostatUpdated': aupd, 'periods': ap, 'geo': annual},
        'monthly': {'eurostatUpdated': mupd, 'periods': mp, 'geo': monthly, 'euReporters': cover},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, separators=(',', ':')))
    print(f'Wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} kB)')


if __name__ == '__main__':
    main()
