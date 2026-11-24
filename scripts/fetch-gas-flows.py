#!/usr/bin/env python3
"""Daily gas supply into the EU by origin / route, from physical flows at the
EU's external border (ENTSOG Transparency Platform) plus LNG send-out (GIE
ALSI). Writes data/gas_flows.json for gassimport.html.

Method (as in Bruegel's import tracker): sum the physical entry flows at the
interconnection points where pipeline gas first enters the EU, grouped by
the supplying country / corridor. Points that are only re-entries of gas
already in the EU (e.g. Melendugno after TAP crosses Albania, Horgos after
TurkStream crosses Serbia) and "pipe-in-pipe" duplicates reported by several
German TSOs for the same physical flow are left out.

Known gaps: Moffat is measured on the GB side, so it includes Northern
Ireland's (UK) supply as well as Ireland's. Yamal (Belarus -> Poland at Kondratki) is no longer published by
ENTSOG; until its stop in May 2022 it is approximated by the onward flow
Poland -> Germany at Mallnow, so Poland's own off-take of Yamal gas is
missing. UK flows include Norwegian gas routed via the UK.

Incremental: an existing file is kept and only the last LOOKBACK days are
refetched (TSOs revise recent days).
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

START = date(2021, 1, 1)  # ENTSOG's API only serves a rolling 5 years;
                           # the file keeps older days once they roll off
LOOKBACK = 45  # days refetched on every run
YAMAL_UNTIL = '2022-05-31'

# route key -> (label, [ENTSOG pointDirection ids])
ROUTES = {
    'NO':      ('Norge', [
        'fr-tso-0003itp-00045entry',   # Dunkerque (Franpipe)
        'be-tso-0001itp-00106entry',   # Zeebrugge ZPT (Zeepipe)
        'dk-tso-0001itp-00630entry',   # Baltic Pipe, North Sea entry
        'de-tso-0009itp-00080entry',   # Emden EPT1 (OGE)
        'de-tso-0005itp-00081entry',   # Emden EPT1 (GUD)
        'nl-tso-0001itp-00160entry',   # Emden EPT1 (GTS)
        'de-tso-0009itp-00126entry',   # Dornum / NETRA (Europipe I+II)
        # Emden NPT (Norpipe) reports no flows; Dornum GASPOOL duplicates NETRA
    ]),
    'RU_NS':   ('Russland: Nord Stream', [
        'de-tso-0017itp-00247entry',   # Greifswald / NEL
        'de-tso-0016itp-00251entry',   # Greifswald / OPAL
    ]),
    'RU_UA':   ('Russland: via Ukraina', [
        'sk-tso-0001itp-00117entry',   # Velke Kapusany
        'hu-tso-0001itp-10006entry',   # VIP Bereg
        'ro-tso-0001itp-00087entry',   # Isaccea I
        'pl-tso-0002itp-10008entry',   # Hermanowice (GCP UA)
    ]),
    'RU_YA':   ('Russland: Yamal (via Belarus)', [
        'de-tso-0001itp-00096entry',   # Mallnow PL->DE (proxy, until May 2022)
    ]),
    'RU_TS':   ('Russland: TurkStream', [
        'bg-tso-0001itp-00549entry',   # Strandzha 2
    ]),
    'RU_BALT': ('Russland: til Finland og Baltikum', [
        'fi-tso-0003itp-00024entry',   # Imatra
        'ee-tso-0001itp-00243entry',   # Narva
        'lv-tso-0001itp-00493entry',   # Luhamaa
    ]),
    'DZ':      ('Algerie', [
        'it-tso-0001itp-00093entry',   # Mazara del Vallo (Transmed)
        'es-tso-0006itp-00048entry',   # Almeria (Medgaz)
        'es-tso-0006itp-00082entry',   # Tarifa (Maghreb-Europe, closed Nov 2021)
    ]),
    'LY':      ('Libya', [
        'it-tso-0001itp-00074entry',   # Gela (Greenstream)
    ]),
    'AZ':      ('Aserbajdsjan (TAP)', [
        'al-tso-0001itp-00274entry',   # Kipoi (TAP entry)
        'gr-tso-0001itp-00046entry',   # Kipi (DESFA, from Turkey)
    ]),
    'UK':      ('Storbritannia', [
        'uk-tso-0004itp-00207entry',   # Bacton BBL -> NL
        'be-tso-0001itp-00061entry',   # Zeebrugge IZT
        'uk-tso-0001itp-00090exit',    # Moffat (GB exit to Ireland, incl. N. Ireland)
    ]),
}

ENTSOG = 'https://transparency.entsog.eu/api/v1/operationalData'
ALSI = 'https://alsi.gie.eu/api'
ALSI_KEY = os.environ.get('AGSI_API_KEY', 'eb259b94628dbfddbaff45e4b3f772a2')

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'data' / 'gas_flows.json'
UA = {'User-Agent': 'Mozilla/5.0 (compatible; gas-flows-fetch/1.0)', 'Accept': 'application/json'}


def get_json(url, headers=None, attempt=0):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        if getattr(e, 'code', None) == 404:
            return {}  # ENTSOG answers 404 when a range has no data
        if attempt < 4:
            wait = 5 * 2 ** attempt
            print(f'  retry {attempt + 1} after {wait}s: {e}', file=sys.stderr)
            time.sleep(wait)
            return get_json(url, headers, attempt + 1)
        raise


def entsog(points, d0, d1):
    """{pointDirection: {date: GWh/d}} for points over [d0, d1]."""
    q = urllib.parse.urlencode({
        'pointDirection': ','.join(points), 'from': d0.isoformat(), 'to': d1.isoformat(),
        'indicator': 'Physical Flow', 'periodType': 'day', 'timezone': 'CET', 'limit': -1,
    })
    rows = get_json(f'{ENTSOG}?{q}').get('operationalData', [])
    if len(rows) >= 9990:
        raise RuntimeError('ENTSOG row cap hit - use smaller batches')
    out = {}
    for r in rows:
        if r.get('value') is None:
            continue
        pd = f"{r['operatorKey']}{r['pointKey']}{r['directionKey']}".lower()
        day = r['periodFrom'][:10]
        # kWh/d -> GWh/d; a few points report more than one row per day
        out.setdefault(pd, {})
        out[pd][day] = out[pd].get(day, 0.0) + r['value'] / 1e6
    return out


def alsi(d0, d1):
    out, page = {}, 1
    while True:
        q = urllib.parse.urlencode({'type': 'eu', 'from': d0.isoformat(), 'to': d1.isoformat(),
                                    'size': 300, 'page': page})
        j = get_json(f'{ALSI}?{q}', {'x-key': ALSI_KEY})
        for r in j.get('data') or []:
            try:
                out[r['gasDayStart']] = float(r['sendOut'])
            except (TypeError, ValueError, KeyError):
                pass
        if page >= int(j.get('last_page') or 1):
            break
        page += 1
        time.sleep(0.5)
    return out


def year_chunks(d0, d1):
    while d0 <= d1:
        end = min(date(d0.year, 12, 31), d1)
        yield d0, end
        d0 = end + timedelta(days=1)


def main():
    today = date.today()
    end = today - timedelta(days=1)
    old = json.loads(OUT.read_text()) if OUT.exists() else None
    if old and old.get('start') == START.isoformat():
        fetch_from = max(START, date.fromisoformat(old['end']) - timedelta(days=LOOKBACK))
    else:
        old, fetch_from = None, START
    print(f'Fetching {fetch_from} .. {end}')

    # day-keyed maps, seeded from the old file
    def unpack(arr):
        return {(START + timedelta(days=i)).isoformat(): v for i, v in enumerate(arr) if v is not None}
    points = {pd: unpack(a) for pd, a in (old or {}).get('points', {}).items()}
    lng = unpack(old['lng']) if old else {}

    all_points = [p for _, pts in ROUTES.values() for p in pts]
    # points added since the last run get their full history
    new = [p for p in all_points if p not in points]
    if new and fetch_from > START:
        print(f'New points, full history: {new}')
        for d0, d1 in year_chunks(START, fetch_from - timedelta(days=1)):
            got = entsog(new, d0, d1)
            for pd in new:
                points.setdefault(pd, {}).update(got.get(pd, {}))
    batches = [all_points[i:i + 9] for i in range(0, len(all_points), 9)]
    for d0, d1 in year_chunks(fetch_from, end):
        for b in batches:
            got = entsog(b, d0, d1)
            for pd in b:
                series = points.setdefault(pd, {})
                for day in list(series):
                    if d0.isoformat() <= day <= d1.isoformat():
                        del series[day]
                series.update(got.get(pd, {}))
        lng.update(alsi(d0, d1))
        print(f'  {d0}..{d1} done')

    # Yamal proxy only while Yamal was flowing
    ya = points.get('de-tso-0001itp-00096entry', {})
    for day in list(ya):
        if day > YAMAL_UNTIL:
            del ya[day]

    n = (end - START).days + 1
    days = [(START + timedelta(days=i)).isoformat() for i in range(n)]
    pack = lambda m: [round(m[d], 1) if d in m else None for d in days]
    # trim trailing days where neither pipelines nor LNG have reported yet
    last = max([d for m in points.values() for d in m] + list(lng) or [START.isoformat()])
    n = days.index(last) + 1 if last in days else n
    doc = {
        'updatedAt': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'unit': 'GWh/d (GCV)',
        'start': START.isoformat(),
        'end': days[n - 1],
        'routes': {k: {'label': lbl, 'points': pts} for k, (lbl, pts) in ROUTES.items()},
        'points': {pd: pack(points.get(pd, {}))[:n] for pd in all_points},
        'lng': pack(lng)[:n],
    }
    OUT.write_text(json.dumps(doc, separators=(',', ':')))
    print(f'Wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} kB), last day {doc["end"]}')


if __name__ == '__main__':
    main()
