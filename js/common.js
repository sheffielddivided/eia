// Shared helpers for elektrisitet.html and gassimport.html (Chart.js 4).
'use strict';

const PAL = {
  blue: '#3987e5', orange: '#d95926', aqua: '#199e70', yellow: '#c98500',
  magenta: '#d55181', green: '#0ca30c', violet: '#9085e9', red: '#e66767',
  brown: '#9c7458', gray: '#6e7681', grayDark: '#4d555e', grayLight: '#9aa4ae', ink: '#c9d1d9',
};
const MONTHS_NO = ['jan', 'feb', 'mar', 'apr', 'mai', 'jun', 'jul', 'aug', 'sep', 'okt', 'nov', 'des'];
const GEO_NAMES = {
  EU27_2020: 'EU27', DE: 'Tyskland', IT: 'Italia', FR: 'Frankrike', NL: 'Nederland', ES: 'Spania',
  PL: 'Polen', BE: 'Belgia', AT: 'Østerrike', HU: 'Ungarn', RO: 'Romania', CZ: 'Tsjekkia',
  IE: 'Irland', PT: 'Portugal', EL: 'Hellas', SK: 'Slovakia', DK: 'Danmark', FI: 'Finland',
  SE: 'Sverige', BG: 'Bulgaria', HR: 'Kroatia', SI: 'Slovenia', LT: 'Litauen', LV: 'Latvia',
  EE: 'Estland', LU: 'Luxembourg', CY: 'Kypros', MT: 'Malta', NO: 'Norge',
};

const nf = (d) => new Intl.NumberFormat('nb-NO', { maximumFractionDigits: d, minimumFractionDigits: d });
function fmt(v, d) { if (v == null || !isFinite(v)) return '–'; return nf(d ?? (Math.abs(v) >= 100 ? 0 : 1)).format(v); }
function fmtPct(v, d = 1) { if (v == null || !isFinite(v)) return '–'; return (v > 0 ? '+' : '') + nf(d).format(v) + ' %'; }
function fmtPp(v, d = 1) { if (v == null || !isFinite(v)) return '–'; return (v > 0 ? '+' : '') + nf(d).format(v) + ' p.p.'; }
const sum = (a) => a.reduce((s, x) => s + (x || 0), 0);
const avg = (a) => { const f = a.filter(x => x != null && isFinite(x)); return f.length ? sum(f) / f.length : null; };
function alpha(hex, a) { const n = parseInt(hex.slice(1), 16); return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`; }

Chart.defaults.color = '#8b949e';
Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif";
Chart.defaults.font.size = 11;
Chart.defaults.borderColor = 'rgba(139,148,158,.14)';
Chart.defaults.plugins.legend.display = false;
Chart.defaults.animation = false;
Object.assign(Chart.defaults.plugins.tooltip, {
  backgroundColor: 'rgba(22,27,34,.96)', borderColor: '#30363d', borderWidth: 1,
  titleColor: '#f0f3f6', bodyColor: '#c9d1d9', padding: 10, boxPadding: 4,
});

const charts = {};
function draw(id, cfg) {
  if (charts[id]) charts[id].destroy();
  charts[id] = new Chart(document.getElementById(id), cfg);
  return charts[id];
}

// items: [{key, label, color, style: 'box'|'line'|'dash'}]
function legend(elId, items, opts = {}) {
  const el = document.getElementById(elId);
  el.innerHTML = '';
  items.forEach((it) => {
    const s = document.createElement('span');
    const cls = it.style === 'line' ? 'line' : it.style === 'dash' ? 'dash' : '';
    s.innerHTML = `<i class="${cls}" style="background:${it.color};border-color:${it.color}"></i>${it.label}`;
    if (opts.hidden && opts.hidden.has(it.key)) s.classList.add('off');
    if (opts.onToggle) s.addEventListener('click', () => opts.onToggle(it));
    else s.style.cursor = 'default';
    el.appendChild(s);
  });
}
function toggleSet(set, key) { set.has(key) ? set.delete(key) : set.add(key); }

const yAxis = (title, extra = {}) => ({
  title: { display: !!title, text: title }, grid: { color: 'rgba(139,148,158,.14)' }, border: { display: false },
  ticks: { callback: v => fmt(v, Math.abs(v) < 10 && v % 1 ? 1 : 0) }, ...extra,
});
const xCat = (extra = {}) => ({ grid: { display: false }, ticks: { maxRotation: 0, autoSkipPadding: 12 }, ...extra });

// Stacked bar chart: series [{key,label,color}], rows, value(row, key)
function stackedBars(id, labels, series, value, opts = {}) {
  return draw(id, {
    type: 'bar',
    data: { labels, datasets: series.map(s => ({
      label: s.label, key: s.key, data: value(s),
      // opts.est: per-bar booleans; estimated bars are drawn pale with a solid outline
      backgroundColor: opts.est ? opts.est.map(e => alpha(s.color, e ? .35 : .87)) : alpha(s.color, .87),
      borderColor: s.color, borderWidth: opts.est ? opts.est.map(e => e ? 1.5 : 0) : 0,
      hidden: opts.hidden?.has(s.key), barPercentage: 1, categoryPercentage: opts.gap ?? .9 })) },
    options: {
      maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
      scales: { x: { ...xCat(), stacked: true }, y: { ...yAxis(opts.yTitle), stacked: true, min: 0, max: opts.max } },
      plugins: {
        annotation: { annotations: opts.annotations || {} },
        tooltip: { filter: (c) => c.parsed.y != null && c.parsed.y !== 0, itemSort: (a, b) => b.datasetIndex - a.datasetIndex,
          callbacks: { title: opts.title, label: (c) => ` ${c.dataset.label}: ${fmt(c.parsed.y, opts.dec)} ${opts.unit || ''}`,
            footer: opts.footer } },
      },
    },
  });
}

function lineChart(id, labels, datasets, opts = {}) {
  return draw(id, {
    type: 'line', data: { labels, datasets },
    options: {
      maintainAspectRatio: false, interaction: { mode: 'index', intersect: false },
      scales: { x: xCat(), y: yAxis(opts.yTitle, opts.y || {}) },
      plugins: {
        annotation: { annotations: opts.annotations || {} },
        tooltip: { filter: (c) => c.parsed.y != null && !String(c.dataset.label).startsWith('_'),
          callbacks: { title: opts.title, label: opts.label || ((c) => ` ${c.dataset.label}: ${fmt(c.parsed.y, opts.dec)} ${opts.unit || ''}`) } },
      },
    },
  });
}
const lineDs = (label, data, color, w = 2, extra = {}) => ({
  label, data, borderColor: color, backgroundColor: color, borderWidth: w, pointRadius: 0, pointHoverRadius: 4, tension: .15, ...extra });

function seg(id, onPick) {
  document.getElementById(id).addEventListener('click', (e) => {
    const b = e.target.closest('button'); if (!b) return;
    [...e.currentTarget.children].forEach(x => x.classList.toggle('active', x === b));
    onPick(b.dataset.v);
  });
}

function tableToggle() {
  document.querySelectorAll('.tbl-toggle[data-t]').forEach(b => b.addEventListener('click', () => {
    const el = document.getElementById(b.dataset.t); el.classList.toggle('open');
    b.dataset.label ??= b.textContent;
    b.textContent = el.classList.contains('open') ? b.dataset.label.replace(/^Vis/, 'Skjul') : b.dataset.label;
  }));
}

function downloadCsv(name, header, rows) {
  const blob = new Blob(['﻿' + [header.join(';'), ...rows.map(r => r.join(';'))].join('\n')], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = name; a.click();
}

function htmlTable(colHead, cols, rows) {
  // rows: [{label, values:[...], bold}]
  const head = `<tr><th>${colHead}</th>${cols.map(c => `<th>${c}</th>`).join('')}</tr>`;
  const body = rows.map(r => `<tr><td>${r.bold ? `<b>${r.label}</b>` : r.label}</td>${r.values.map(v => `<td>${r.bold ? `<b>${fmt(v, 1)}</b>` : fmt(v, 1)}</td>`).join('')}</tr>`).join('');
  return `<table>${head}${body}</table>`;
}

function loadPage(url, onData) {
  fetch(url, { cache: 'no-cache' })
    .then(r => { if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`); return r.json(); })
    .then(onData)
    .catch(e => { document.getElementById('app').innerHTML = `<div class="error">Kunne ikke laste data (${e.message}).</div>`; console.error(e); });
}
function mountTemplate() {
  const app = document.getElementById('app');
  app.innerHTML = '';
  app.appendChild(document.getElementById('tpl').content.cloneNode(true));
}
const ymLabel = (m) => `${MONTHS_NO[+m.slice(5, 7) - 1]} ${m.slice(2, 4)}`;
const ymLong = (m) => `${MONTHS_NO[+m.slice(5, 7) - 1]} ${m.slice(0, 4)}`;
