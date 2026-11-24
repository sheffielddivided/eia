#!/usr/bin/env python3
"""Fetch where Europe's natural gas comes from (Eurostat) and write
data/eurostat/gas_imports.json for gassimport.html.

  nrg_ti_gas   Annual imports by partner country, 1990 -> ~1.5 yrs ago
  nrg_ti_gasm  Monthly imports by partner country, 2008 -> ~1-2 months ago
  nrg_cb_gas / nrg_cb_gasm  Indigenous production (IPRD), for context

siec G3000 = all natural gas imports, G3200 = the LNG part of it, so
pipeline = G3000 - G3200 per partner.

Partners are mapped to origin groups. Monthly data is largely reported by the
last country the gas crossed, so transit countries are mapped to the known
origin of the pipe gas flowing through them:
  UA, BY, RS (+ EX_SU_OTH pre-1991)  -> Russia (Brotherhood/Yamal/TurkStream)
  TN, MA                            -> Algeria (Transmed / Maghreb-Europe)
  AL, GE                            -> Azerbaijan (TAP / South Caucasus)
  TR is kept separate: mostly Russian gas via TurkStream, some Azeri via TANAP.
Intra-EU partners are dropped for the EU27 aggregate (internal trade) and
kept as 'via EU' for single countries.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from eurostat_common import EU27, fetch, periods, records

GEOS = ['EU27_2020', 'DE', 'IT', 'FR', 'NL', 'ES', 'BE', 'PL', 'AT', 'HU', 'EL',
        'PT', 'CZ', 'SK', 'BG', 'RO', 'HR', 'LT', 'FI', 'IE', 'DK', 'SE']

GROUP_OF = {
    'NO': 'NO',
    'RU': 'RU', 'UA': 'RU', 'BY': 'RU', 'RS': 'RU', 'EX_SU_OTH': 'RU',
    'TR': 'TR',
    'DZ': 'DZ', 'TN': 'DZ', 'MA': 'DZ',
    'AZ': 'AZ', 'AL': 'AZ', 'GE': 'AZ',
    'US': 'US', 'QA': 'QA', 'NG': 'NG', 'UK': 'UK', 'LY': 'LY',
    'NSP': 'NSP',
}
for c in EU27:
    GROUP_OF[c] = 'EU'
SKIP = {'TOTAL'}

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'data' / 'eurostat' / 'gas_imports.json'


def add(d, *keys_and_val):
    *keys, last, val = keys_and_val
    for k in keys:
        d = d.setdefault(k, {})
    d[last] = d.get(last, 0.0) + val


def build_imports(dataset):
    print(f'[{dataset}] ...')
    out, all_p, upd = {}, set(), None
    for geo in GEOS:
        try:
            js = fetch(dataset, [('geo', geo), ('siec', 'G3000'), ('siec', 'G3200'),
                                 ('unit', 'TJ_GCV'), ('unit', 'MIO_M3')])
        except Exception as e:
            print(f'  {geo}: FAILED {e}', file=sys.stderr)
            continue
        g3000, g3200, tj_tot, m3_tot = {}, {}, {}, {}
        for co, v in records(js):
            t, p, s, u = co['time'], co['partner'], co['siec'], co['unit']
            if p == 'TOTAL' and s == 'G3000':
                (tj_tot if u == 'TJ_GCV' else m3_tot)[t] = v
                continue
            if u != 'TJ_GCV' or p in SKIP:
                continue
            grp = GROUP_OF.get(p, 'OTH')
            if geo == 'EU27_2020' and grp == 'EU':
                continue
            add(g3000 if s == 'G3000' else g3200, grp, t, v)
        pipe = {}
        for grp, series in g3000.items():
            for t, v in series.items():
                lng = g3200.get(grp, {}).get(t, 0.0)
                pipe.setdefault(grp, {})[t] = round(max(v - lng, 0.0))
        lng = {g: {t: round(v) for t, v in s.items() if v} for g, s in g3200.items()}
        pipe = {g: {t: v for t, v in s.items() if v} for g, s in pipe.items()}
        m3f = {t: round(m3_tot[t] / tj_tot[t], 7) for t in tj_tot if m3_tot.get(t) and tj_tot[t]}
        out[geo] = {'pipe': pipe, 'lng': lng, 'm3PerTJ': m3f}
        ps = periods(js)
        all_p.update(ps)
        upd = max(filter(None, [upd, js.get('updated')]))
        print(f'  {geo}: {len(ps)} periods, groups {sorted(set(pipe) | set(lng))}')
    return out, sorted(all_p), upd


def add_production(dataset, data, bal):
    print(f'[{dataset}] production ...')
    for geo in data:
        try:
            js = fetch(dataset, [('geo', geo), ('siec', 'G3000'), ('unit', 'TJ_GCV'), ('nrg_bal', bal)])
        except Exception as e:
            print(f'  {geo}: FAILED {e}', file=sys.stderr)
            continue
        data[geo]['prod'] = {co['time']: round(v) for co, v in records(js) if v}


def main():
    annual, ap, aupd = build_imports('nrg_ti_gas')
    monthly, mp, mupd = build_imports('nrg_ti_gasm')
    if 'EU27_2020' not in annual or 'EU27_2020' not in monthly:
        raise SystemExit('EU27 missing - aborting without writing')
    add_production('nrg_cb_gas', annual, 'IPRD')
    add_production('nrg_cb_gasm', monthly, 'IPRD')
    doc = {
        'updatedAt': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'unit': 'TJ_GCV',
        'geos': GEOS,
        'annual': {'eurostatUpdated': aupd, 'periods': ap, 'geo': annual},
        'monthly': {'eurostatUpdated': mupd, 'periods': mp, 'geo': monthly},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, separators=(',', ':')))
    print(f'Wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} kB)')


if __name__ == '__main__':
    main()
