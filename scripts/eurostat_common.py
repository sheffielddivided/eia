"""Shared helpers for the Eurostat fetch scripts (JSON-stat 2.0 API, no key)."""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API = 'https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data'

EU27 = ['BE', 'BG', 'CZ', 'DK', 'DE', 'EE', 'IE', 'EL', 'ES', 'FR', 'HR', 'IT',
        'CY', 'LV', 'LT', 'LU', 'HU', 'MT', 'NL', 'AT', 'PL', 'PT', 'RO', 'SI',
        'SK', 'FI', 'SE']


def fetch(dataset, params, attempt=0):
    """GET one dataset slice. params: list of (key, value) tuples."""
    q = [('format', 'JSON'), ('lang', 'en')] + list(params)
    url = f'{API}/{dataset}?{urllib.parse.urlencode(q)}'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (compatible; eurostat-data-fetch/1.0)',
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


def records(js):
    """Yield (coord dict, value) for every non-null cell of a JSON-stat reply."""
    ids, size = js['id'], js['size']
    cats = {k: js['dimension'][k]['category']['index'] for k in ids}
    inv = {k: {v: c for c, v in cats[k].items()} for k in ids}
    strides = [1] * len(ids)
    for i in range(len(ids) - 2, -1, -1):
        strides[i] = strides[i + 1] * size[i + 1]
    for flat, val in js['value'].items():
        if val is None:
            continue
        flat = int(flat)
        yield {k: inv[k][(flat // strides[i]) % size[i]] for i, k in enumerate(ids)}, float(val)


def periods(js):
    idx = js['dimension']['time']['category']['index']
    return sorted(idx, key=idx.get)
