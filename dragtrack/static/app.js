/* StoreScope dashboard */
(() => {
"use strict";

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const sum = (arr, f = (x) => x) => arr.reduce((a, x) => a + (f(x) || 0), 0);
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const DAY = 86400000;

const state = {
  cfg: {currency: "USD", poll_interval: 30},
  products: [], productById: new Map(),
  players: [], playerById: new Map(),
  purchases: [], purchaseById: new Map(), lastId: 0,
  health: null, events: [], logs: [],
  page: "overview", search: "",
  feed: {page: 0, cat: "all", source: "all", review: false, range: "all", perPage: 50},
  players_sort: "spent", overview_range: 30, analytics_range: 90,
  online: false, newIds: new Set(), fontsReady: false,
};

let moneyFmt = null;
function money(v, opts = {}) {
  if (v == null || Number.isNaN(v)) return "?";
  if (!moneyFmt) {
    try { moneyFmt = new Intl.NumberFormat(undefined, {style: "currency", currency: state.cfg.currency || "USD"}); }
    catch { moneyFmt = new Intl.NumberFormat(undefined, {style: "currency", currency: "USD"}); }
  }
  if (opts.compact && Math.abs(v) >= 10000) {
    return moneyFmt.formatToParts(1).filter((p) => p.type === "currency").map((p) => p.value).join("") + (v / 1000).toFixed(1) + "K";
  }
  return moneyFmt.format(v);
}
const num = (v) => new Intl.NumberFormat().format(v);
const plural = (n, word) => `${num(n)} ${word}${n === 1 ? "" : "s"}`;
const pct = (v) => (v * 100).toFixed(v >= 0.1 ? 0 : 1) + "%";
const parseTs = (iso) => new Date(iso);
const dayKey = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const startOfDay = (d) => { const x = new Date(d); x.setHours(0, 0, 0, 0); return x; };
const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const startOfWeek = (d) => { const x = startOfDay(d); const dow = (x.getDay() + 6) % 7; x.setDate(x.getDate() - dow); return x; };
const monthKey = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
const fmtDate = (d) => d.toLocaleDateString(undefined, {month: "short", day: "numeric"});
const fmtDateY = (d) => d.toLocaleDateString(undefined, {year: "numeric", month: "short", day: "numeric"});
const fmtTime = (d) => d.toLocaleTimeString(undefined, {hour: "2-digit", minute: "2-digit"});
const fmtFull = (d) => d.toLocaleString(undefined, {year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit"});
function rel(d, now = new Date()) {
  const s = Math.round((now - d) / 1000);
  if (s < 0) return "in the future";
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 86400 * 2) return "yesterday";
  if (s < 86400 * 14) return `${Math.floor(s / 86400)}d ago`;
  return fmtDate(d);
}
const fmtDur = (s) => {
  if (s == null) return "—";
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m ${s % 60}s`;
};
const bytes = (b) => b > 1e6 ? (b / 1e6).toFixed(1) + " MB" : b > 1e3 ? (b / 1e3).toFixed(0) + " KB" : b + " B";
const avatarUrl = (uuid, size = 32) => uuid?.startsWith("name:") ? `https://minotar.net/helm/${encodeURIComponent(uuid.slice(5))}/${size}` : `https://minotar.net/helm/${uuid}/${size}`;
const bustUrl = (uuid) => uuid?.startsWith("name:") ? `https://minotar.net/armor/bust/${encodeURIComponent(uuid.slice(5))}/120` : `https://minotar.net/armor/bust/${uuid}/120`;
const productUrl = (p) => p.package_id ? `${state.cfg.store_url}/package/${p.package_id}` : null;

async function api(path, opts) {
  const r = await fetch(path, {cache: "no-store", ...opts});
  if (r.status === 401) { location.href = "/login?next=" + encodeURIComponent(location.pathname + location.hash); throw new Error("signed out"); }
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

// ---------------------------------------------------------------------------
// theme
// ---------------------------------------------------------------------------
const THEMES = [
  {name: "Midnight Violet", accent: "#9b5de5"},
  {name: "Lavender", accent: "#b39ddb"},
  {name: "Steel", accent: "#4f8fbf"},
  {name: "Crimson", accent: "#e03131"},
  {name: "Emerald", accent: "#2fb344"},
  {name: "Ember", accent: "#ff7a33"},
  {name: "Azure", accent: "#1e90ff"},
];
function applyTheme(name) {
  const t = THEMES.find((x) => x.name === name) || THEMES[0];
  document.documentElement.style.setProperty("--accent", t.accent);
  $("#theme-name").textContent = t.name;
  $$("#theme-dots .theme-dot").forEach((d) => d.classList.toggle("active", d.dataset.name === t.name));
  try { localStorage.setItem("dragtrack.theme", t.name); } catch {}
  renderPage();
}
function initTheme() {
  $("#theme-dots").innerHTML = THEMES.map((t) => `<button class="theme-dot" data-name="${esc(t.name)}" title="${esc(t.name)}" style="background:${t.accent}"></button>`).join("");
  $("#theme-dots").addEventListener("click", (e) => { const b = e.target.closest(".theme-dot"); if (b) applyTheme(b.dataset.name); });
  let saved = null; try { saved = localStorage.getItem("dragtrack.theme"); } catch {}
  const t = THEMES.find((x) => x.name === saved) || THEMES[0];
  document.documentElement.style.setProperty("--accent", t.accent);
  $("#theme-name").textContent = t.name;
  $$("#theme-dots .theme-dot").forEach((d) => d.classList.toggle("active", d.dataset.name === t.name));
}

// category colours: fixed per entity, never by rank
const CAT_FIXED = {gems: 1, ranks: 2, boosters: 3};
const catSlot = new Map();
function catColor(cat) {
  const key = (cat || "unknown").toLowerCase();
  if (key === "unknown") return "var(--muted)";
  if (!catSlot.has(key)) {
    const fixed = CAT_FIXED[key];
    if (fixed) catSlot.set(key, fixed);
    else {
      const used = new Set(catSlot.values());
      let s = 4; while (used.has(s) && s < 8) s++;
      catSlot.set(key, s);
    }
  }
  return `var(--cat-${catSlot.get(key)})`;
}
function catTag(cat) {
  return `<span class="tag"><i style="background:${catColor(cat)}"></i>${esc(cat || "Unknown")}</span>`;
}

// ---------------------------------------------------------------------------
// data
// ---------------------------------------------------------------------------
function indexProducts(list) {
  state.products = list;
  state.productById = new Map(list.map((p) => [p.package_id, p]));
  Object.keys(CAT_FIXED).forEach(catColor);
  [...new Set(list.map((p) => p.category))].sort().forEach(catColor);
}
function indexPlayers(list) {
  state.players = list;
  state.playerById = new Map(list.map((p) => [p.uuid, p]));
}
function addPurchases(list) {
  let added = 0;
  for (const p of list) {
    p.ts = parseTs(p.purchased_at);
    if (state.purchaseById.has(p.id)) { Object.assign(state.purchaseById.get(p.id), p); continue; }
    state.purchases.push(p); state.purchaseById.set(p.id, p); added++;
    state.lastId = Math.max(state.lastId, p.id);
  }
  if (added) state.purchases.sort((a, b) => a.ts - b.ts || a.id - b.id);
  return added;
}

async function loadAll() {
  const boot = await api("/api/bootstrap");
  state.cfg = boot.config; moneyFmt = null;
  indexProducts(boot.products); indexPlayers(boot.players);
  state.health = boot.health; state.events = boot.events;
  $("#version").textContent = "v" + boot.config.version;
  $("#signout").hidden = !boot.config.auth;
  document.title = `StoreScope · ${boot.config.store_name}`;
  let since = 0;
  for (;;) {
    const r = await api(`/api/purchases?since_id=${since}&limit=5000`);
    addPurchases(r.purchases);
    if (r.purchases.length < 5000) break;
    since = state.lastId;
  }
  state.online = true;
}

async function tick() {
  try {
    const r = await api(`/api/purchases?since_id=${state.lastId}&limit=5000`);
    const fresh = r.purchases;
    if (fresh.length) {
      addPurchases(fresh);
      fresh.forEach((p) => state.newIds.add(p.id));
      const pl = await api("/api/players"); indexPlayers(pl.players);
      fresh.slice(-3).forEach((p) => toast(`${p.username} bought ${p.product_name || p.product_raw}`, priceText(p) + (p.source === "seed" ? " · seeded" : "")));
      if (fresh.length > 3) toast(`+${fresh.length - 3} more purchases`, "", true);
      renderPage();
      setTimeout(() => { state.newIds.clear(); }, 4000);
    }
    state.online = true;
  } catch (e) {
    state.online = false;
  }
  updatePill();
}
async function tickHealth() {
  try {
    const [h, ev] = await Promise.all([api("/api/health"), api("/api/events?limit=60")]);
    state.health = h; state.events = ev.events; state.online = true;
    if (state.page === "status") { state.logs = (await api("/api/logs?limit=200")).logs; renderPage(); }
    else if (state.page === "overview") renderPage();
  } catch { state.online = false; }
  updatePill();
}
async function tickProducts() {
  try { const r = await api("/api/products"); indexProducts(r.products); state.priceHistory = r.price_history; if (state.page === "products") renderPage(); } catch {}
}

function updatePill() {
  const pill = $("#live-pill"), h = state.health;
  let cls = "", text = "offline";
  if (state.online && h) { cls = h.status; text = {ok: "live", degraded: "degraded", blocked: "blocked", starting: "starting"}[h.status] || h.status; }
  pill.className = "live-pill " + cls; $("#live-text").textContent = text;
  const bad = h && h.status !== "ok" && h.status !== "starting";
  const b = $("#badge-status"); b.textContent = bad ? "!" : ""; b.classList.toggle("hot", !!bad);
  const review = state.purchases.filter(needsReview).length;
  const bf = $("#badge-feed"); bf.textContent = review ? `${review} review` : num(state.purchases.length); bf.classList.toggle("hot", review > 0);
  $("#badge-players").textContent = num(state.players.length);
  $("#badge-products").textContent = num(state.products.filter((p) => p.active).length);
}

// ---------------------------------------------------------------------------
// stats
// ---------------------------------------------------------------------------
const needsReview = (p) => !p.resolved && (p.ambiguous || p.price == null);
const priceOf = (p) => p.price ?? 0;
function priceText(p) {
  if (p.price == null) return "unknown price";
  if (p.ambiguous && !p.resolved) return `${money(p.price_min)} – ${money(p.price_max)}`;
  return money(p.price);
}
function revenue(list) {
  let total = 0, min = 0, max = 0, approx = false, unknown = 0;
  for (const p of list) {
    if (p.price == null) { unknown++; approx = true; continue; }
    total += p.price;
    if (p.ambiguous && !p.resolved) { min += p.price_min ?? p.price; max += p.price_max ?? p.price; approx = true; }
    else { min += p.price; max += p.price; }
  }
  return {total, min, max, approx, unknown, count: list.length};
}
function moneyApprox(r, cls = "") {
  const tip = r.approx ? `data-tip="${esc(`Range ${money(r.min)} – ${money(r.max)}${r.unknown ? ` · ${r.unknown} with unknown price` : ""}`)}"` : "";
  return `<span class="${cls}" ${tip}>${r.approx ? '<span class="approx">≈</span>' : ""}${money(r.total)}</span>`;
}
function inRange(from, to) { return state.purchases.filter((p) => p.ts >= from && p.ts < to); }
function byDay(list, from, days) {
  const map = new Map(); for (let i = 0; i < days; i++) map.set(dayKey(addDays(from, i)), {rev: 0, n: 0, date: addDays(from, i)});
  for (const p of list) { const k = dayKey(p.ts); const b = map.get(k); if (b) { b.rev += priceOf(p); b.n++; } }
  return [...map.values()];
}
function playerStats() {
  const m = new Map();
  for (const p of state.purchases) {
    let s = m.get(p.uuid);
    if (!s) { s = {uuid: p.uuid, username: p.username, total: 0, min: 0, max: 0, count: 0, first: p.ts, last: p.ts, cats: {}, prods: {}, approx: false, days: new Set()}; m.set(p.uuid, s); }
    const pr = priceOf(p); s.total += pr; s.count++;
    if (p.ambiguous && !p.resolved) { s.min += p.price_min ?? pr; s.max += p.price_max ?? pr; s.approx = true; } else { s.min += pr; s.max += pr; }
    if (p.price == null) s.approx = true;
    if (p.ts < s.first) s.first = p.ts; if (p.ts >= s.last) { s.last = p.ts; s.username = p.username; }
    const c = p.category || "Unknown"; s.cats[c] = (s.cats[c] || 0) + pr;
    const n = p.product_name || p.product_raw; const pp = s.prods[n] || (s.prods[n] = {count: 0, amount: 0, category: p.category}); pp.count++; pp.amount += pr;
    s.days.add(dayKey(p.ts));
  }
  for (const s of m.values()) {
    const pl = state.playerById.get(s.uuid); if (pl) s.username = pl.username;
    s.fav = Object.entries(s.prods).sort((a, b) => b[1].count - a[1].count || b[1].amount - a[1].amount)[0]?.[0] || "—";
    s.avg = s.count ? s.total / s.count : 0;
  }
  return [...m.values()];
}
function productStats() {
  const m = new Map();
  for (const p of state.products) m.set(p.package_id, {product: p, count: 0, revenue: 0, last: null, possible: 0});
  const unknown = [];
  for (const p of state.purchases) {
    if (p.package_id && m.has(p.package_id)) { const s = m.get(p.package_id); s.count++; s.revenue += priceOf(p); if (!s.last || p.ts > s.last) s.last = p.ts; }
    else if (p.ambiguous && !p.resolved) { for (const c of p.candidates || []) { const s = m.get(c); if (s) s.possible++; } }
    else unknown.push(p);
  }
  return {stats: [...m.values()], unknown};
}

// ---------------------------------------------------------------------------
// charts (inline SVG)
// ---------------------------------------------------------------------------
function niceMax(v) {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v))); const f = v / p;
  const n = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
  return n * p;
}
function roundTopRect(x, y, w, h, r) {
  if (h <= r * 1.5 || w <= r * 2) return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${Math.min(2, h / 2)}"/>`;
  return `<path d="M${x},${y + r} a${r},${r} 0 0 1 ${r},-${r} h${w - 2 * r} a${r},${r} 0 0 1 ${r},${r} v${h - r} h-${w} z"/>`;
}
function svgOpen(w, h) { return `<svg class="chart" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img">`; }
const fmtAxisMoney = (v) => v >= 1000 ? money(v / 1000).replace(/\.00/, "") + "K" : money(v).replace(/\.00$/, "");

function axisMax(v, integer) { return integer ? Math.max(4, 4 * Math.ceil(v / 4)) : niceMax(v); }
function barChart({width, height = 220, data, yFmt = fmtAxisMoney, xLabel = (d) => d.label, color, integer = false}) {
  const m = {l: 48, r: 12, t: 14, b: 28}, W = Math.max(200, width), H = height, iw = W - m.l - m.r, ih = H - m.t - m.b;
  const n = data.length || 1, slot = iw / n, bw = Math.min(24, slot * 0.68), yMax = axisMax(Math.max(...data.map((d) => d.value), 0), integer);
  const y = (v) => m.t + ih - (v / yMax) * ih;
  let s = svgOpen(W, H) + `<g class="grid">`;
  for (let i = 0; i <= 4; i++) { const v = (yMax / 4) * i; s += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"/><text x="${m.l - 8}" y="${y(v) + 4}" text-anchor="end">${esc(yFmt(v))}</text>`; }
  s += `</g><g class="axis"><line x1="${m.l}" x2="${W - m.r}" y1="${m.t + ih}" y2="${m.t + ih}"/></g>`;
  const every = Math.max(1, Math.ceil(n / Math.floor(iw / 58)));
  data.forEach((d, i) => {
    const x = m.l + i * slot, bx = x + (slot - bw) / 2, by = y(d.value), h = m.t + ih - by;
    if (d.value > 0) s += `<g class="bar" ${color ? `style="fill:${color}"` : ""}>${roundTopRect(bx, by, bw, h, 4)}</g>`;
    if (i % every === 0) s += `<text x="${x + slot / 2}" y="${H - 8}" text-anchor="middle">${esc(xLabel(d))}</text>`;
    s += `<rect class="hit" x="${x}" y="${m.t}" width="${slot}" height="${ih}" data-tip="${esc(d.tip || "")}"/>`;
  });
  return s + `</svg>`;
}
function stackedChart({width, height = 240, data, series, yFmt = fmtAxisMoney, xLabel = (d) => d.label, integer = false}) {
  const m = {l: 48, r: 12, t: 14, b: 28}, W = Math.max(200, width), H = height, iw = W - m.l - m.r, ih = H - m.t - m.b;
  const n = data.length || 1, slot = iw / n, bw = Math.min(24, slot * 0.68);
  const yMax = axisMax(Math.max(...data.map((d) => sum(series, (k) => d.values[k.key] || 0)), 0), integer);
  const y = (v) => m.t + ih - (v / yMax) * ih;
  let s = svgOpen(W, H) + `<g class="grid">`;
  for (let i = 0; i <= 4; i++) { const v = (yMax / 4) * i; s += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"/><text x="${m.l - 8}" y="${y(v) + 4}" text-anchor="end">${esc(yFmt(v))}</text>`; }
  s += `</g><g class="axis"><line x1="${m.l}" x2="${W - m.r}" y1="${m.t + ih}" y2="${m.t + ih}"/></g>`;
  const every = Math.max(1, Math.ceil(n / Math.floor(iw / 58)));
  data.forEach((d, i) => {
    const x = m.l + i * slot, bx = x + (slot - bw) / 2; let acc = 0; const parts = [];
    series.forEach((k, si) => {
      const v = d.values[k.key] || 0; if (v <= 0) return;
      const top = y(acc + v), bottom = y(acc); acc += v;
      const gap = si > 0 || parts.length ? 2 : 0;
      const h = Math.max(0, bottom - top - gap);
      parts.push(`<rect x="${bx}" y="${top}" width="${bw}" height="${h}" rx="${parts.length === 0 ? 0 : 0}" fill="${k.color}"/>`);
    });
    s += parts.join("");
    if (i % every === 0) s += `<text x="${x + slot / 2}" y="${H - 8}" text-anchor="middle">${esc(xLabel(d))}</text>`;
    const full = integer ? (v) => num(v) : yFmtFull;
    const tip = `<b>${esc(d.label)}</b><br>` + series.map((k) => `<span class="k">${esc(k.label)}</span> ${esc(full(d.values[k.key] || 0))}`).join("<br>") + `<br><span class="k">total</span> ${esc(full(sum(series, (k) => d.values[k.key] || 0)))}`;
    s += `<rect class="hit" x="${x}" y="${m.t}" width="${slot}" height="${ih}" data-tip="${esc(tip)}"/>`;
  });
  return s + `</svg>`;
}
const yFmtFull = (v) => money(v);
function areaChart({width, height = 220, points, yFmt = fmtAxisMoney}) {
  const m = {l: 52, r: 16, t: 14, b: 28}, W = Math.max(200, width), H = height, iw = W - m.l - m.r, ih = H - m.t - m.b;
  const n = points.length; if (n < 2) return `<div class="empty">Not enough data yet</div>`;
  const yMax = niceMax(Math.max(...points.map((p) => p.value), 0));
  const x = (i) => m.l + (i / (n - 1)) * iw, y = (v) => m.t + ih - (v / yMax) * ih;
  let s = svgOpen(W, H) + `<g class="grid">`;
  for (let i = 0; i <= 4; i++) { const v = (yMax / 4) * i; s += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}"/><text x="${m.l - 8}" y="${y(v) + 4}" text-anchor="end">${esc(yFmt(v))}</text>`; }
  s += `</g>`;
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  s += `<path class="area" d="${path} L${x(n - 1)},${m.t + ih} L${x(0)},${m.t + ih} Z"/><path class="line" d="${path}"/>`;
  s += `<circle class="dot" cx="${x(n - 1)}" cy="${y(points[n - 1].value)}" r="4"/>`;
  s += `<text class="label" x="${x(n - 1) - 6}" y="${y(points[n - 1].value) - 10}" text-anchor="end">${esc(yFmtFull(points[n - 1].value))}</text>`;
  const every = Math.max(1, Math.ceil(n / Math.floor(iw / 70)));
  points.forEach((p, i) => {
    if (i % every === 0 || i === n - 1) s += `<text x="${x(i)}" y="${H - 8}" text-anchor="${i === n - 1 ? "end" : i === 0 ? "start" : "middle"}">${esc(p.label)}</text>`;
    const slot = iw / n; s += `<rect class="hit" x="${x(i) - slot / 2}" y="${m.t}" width="${slot}" height="${ih}" data-tip="${esc(p.tip || "")}"/>`;
  });
  return s + `</svg>`;
}
function hbarChart({width, rows, fmt = money}) {
  const labelW = Math.min(220, Math.max(120, width * 0.34)), rowH = 26, W = Math.max(240, width), H = rows.length * rowH + 4;
  const max = Math.max(...rows.map((r) => r.value), 0) || 1, barMax = W - labelW - 90;
  let s = svgOpen(W, H);
  rows.forEach((r, i) => {
    const y = i * rowH + 3, w = Math.max(2, (r.value / max) * barMax);
    const label = r.label.length > 30 ? r.label.slice(0, 29) + "…" : r.label;
    s += `<text x="${labelW - 10}" y="${y + 14}" text-anchor="end" class="label">${esc(label)}</text>`;
    s += `<g class="hbar" ${r.color ? `style="fill:${r.color}"` : `style="fill:var(--accent)"`}><path d="M${labelW},${y + 3} h${Math.max(0, w - 4)} a4,4 0 0 1 4,4 v10 a4,4 0 0 1 -4,4 h-${Math.max(0, w - 4)} z"/></g>`;
    s += `<text x="${labelW + w + 8}" y="${y + 14}" class="label">${esc(fmt(r.value))}${r.extra ? ` <tspan fill="var(--muted)">${esc(r.extra)}</tspan>` : ""}</text>`;
    s += `<rect class="hit" x="0" y="${y - 3}" width="${W}" height="${rowH}" data-tip="${esc(r.tip || "")}"/>`;
  });
  return s + `</svg>`;
}
function heatmap({width, grid, rowLabels, colLabels, fmt}) {
  const m = {l: 36, t: 18}, W = Math.max(300, width), cols = colLabels.length, rows = rowLabels.length;
  const cw = (W - m.l - 4) / cols, ch = Math.min(26, Math.max(16, cw)), H = m.t + rows * ch + 4;
  const max = Math.max(...grid.flat(), 0) || 1;
  let s = svgOpen(W, H);
  colLabels.forEach((c, i) => { if (i % 3 === 0) s += `<text x="${m.l + i * cw + cw / 2}" y="12" text-anchor="middle">${esc(c)}</text>`; });
  rowLabels.forEach((r, i) => s += `<text x="${m.l - 8}" y="${m.t + i * ch + ch / 2 + 4}" text-anchor="end">${esc(r)}</text>`);
  grid.forEach((row, ri) => row.forEach((v, ci) => {
    const op = v ? 0.12 + 0.88 * (v / max) : 0.05;
    s += `<rect class="cell" x="${m.l + ci * cw + 1}" y="${m.t + ri * ch + 1}" width="${cw - 2}" height="${ch - 2}" rx="3" fill="var(--accent)" opacity="${op.toFixed(2)}" data-tip="${esc(`<b>${rowLabels[ri]} ${colLabels[ci]}:00</b><br>${fmt(v)}`)}"/>`;
  }));
  return s + `</svg>`;
}
function sparkline(values, w = 92, h = 30) {
  if (!values.length) return "";
  const max = Math.max(...values, 0) || 1, n = values.length;
  const x = (i) => (i / Math.max(1, n - 1)) * (w - 6) + 3, y = (v) => h - 4 - (v / max) * (h - 8);
  const d = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><path d="${d} L${x(n - 1)},${h - 2} L${x(0)},${h - 2} Z" fill="var(--accent)" opacity=".12"/><path d="${d}" fill="none" stroke="var(--accent)" stroke-width="1.5" stroke-linejoin="round"/><circle cx="${x(n - 1)}" cy="${y(values[n - 1])}" r="2.5" fill="var(--accent)"/></svg>`;
}
function stackBar(parts) {
  const total = sum(parts, (p) => p.value) || 1;
  const bar = `<div class="stackbar">${parts.filter((p) => p.value > 0).map((p) => `<span style="flex:${p.value};background:${p.color}" data-tip="${esc(`<b>${p.label}</b><br>${money(p.value)} · ${pct(p.value / total)}`)}"></span>`).join("")}</div>`;
  const legend = `<div class="legend">${parts.map((p) => `<span><i style="background:${p.color}"></i>${esc(p.label)} <b>${money(p.value)}</b> <span class="muted">${pct(p.value / total)}</span></span>`).join("")}</div>`;
  return bar + legend;
}
const legend = (series) => `<div class="legend">${series.map((k) => `<span><i style="background:${k.color}"></i>${esc(k.label)}</span>`).join("")}</div>`;

// ---------------------------------------------------------------------------
// pieces
// ---------------------------------------------------------------------------
const statTile = ({label, value, delta, spark, foot, hero, tip}) => `
  <div class="card stat ${hero ? "hero hl" : ""}" ${tip ? `data-tip="${esc(tip)}"` : ""}>
    ${spark ? sparkline(spark) : ""}
    <div class="label">${esc(label)}</div>
    <div class="value">${value}</div>
    ${delta ? `<div class="delta ${delta.cls}">${delta.text}</div>` : ""}
    ${foot ? `<div class="foot">${foot}</div>` : ""}
  </div>`;
function deltaOf(cur, prev, label) {
  if (!prev && !cur) return {cls: "", text: `no data vs ${label}`};
  if (!prev) return {cls: "up", text: `▲ new vs ${label}`};
  const d = (cur - prev) / prev;
  return {cls: d >= 0 ? "up" : "down", text: `${d >= 0 ? "▲" : "▼"} ${Math.abs(d * 100).toFixed(0)}% vs ${label}`};
}
function who(p, size = 32) {
  const pl = state.playerById.get(p.uuid);
  return `<span class="who"><img class="avatar" src="${avatarUrl(p.uuid, size)}" alt="" loading="lazy"><span><div class="name">${esc(pl?.username || p.username)}</div></span></span>`;
}
function purchaseRow(p) {
  const amt = p.price == null ? `<span class="money range">?</span>` : `<span class="money ${p.ambiguous && !p.resolved ? "range" : ""}">${p.ambiguous && !p.resolved ? "≈" : ""}${money(p.price)}</span>`;
  return `<div class="row ${state.newIds.has(p.id) ? "new" : ""}">
    <img class="avatar" src="${avatarUrl(p.uuid)}" alt="" loading="lazy">
    <div class="what"><div class="p"><b>${esc(p.username)}</b> · ${esc(p.product_name || p.product_raw)}</div><div class="t">${rel(p.ts)} · ${esc(p.category || "Unknown")}${p.source === "seed" ? " · seeded" : ""}</div></div>
    <div class="amt ${p.ambiguous && !p.resolved ? "range" : ""}" data-tip="${esc(priceText(p))}">${amt}</div></div>`;
}

// ---------------------------------------------------------------------------
// pages
// ---------------------------------------------------------------------------
const PAGES = {
  overview: {title: "Overview", sub: () => `${state.cfg.store_name} store`},
  feed: {title: "Live Feed", sub: () => plural(state.purchases.length, "purchase")},
  players: {title: "Players", sub: () => `${num(state.players.length)} tracked`},
  products: {title: "Products", sub: () => `${num(state.products.filter((p) => p.active).length)} in store`},
  analytics: {title: "Analytics", sub: () => "trends & patterns"},
  status: {title: "Status", sub: () => state.health ? `tracker ${state.health.status}` : ""},
};

function contentWidth() { return Math.max(300, $("#content").clientWidth - 64); }
function cardWidth(cols) { const w = contentWidth(); return Math.floor((w - 16 * (cols - 1)) / cols) - 42; }

// ---- overview ---------------------------------------------------------------
function renderOverview(el) {
  const now = new Date(), today = startOfDay(now);
  const all = revenue(state.purchases);
  const todayR = revenue(inRange(today, addDays(today, 1))), yest = revenue(inRange(addDays(today, -1), today));
  const d7 = revenue(inRange(addDays(today, -6), addDays(today, 1))), p7 = revenue(inRange(addDays(today, -13), addDays(today, -6)));
  const d30 = revenue(inRange(addDays(today, -29), addDays(today, 1))), p30 = revenue(inRange(addDays(today, -59), addDays(today, -29)));
  const days14 = byDay(inRange(addDays(today, -13), addDays(today, 1)), addDays(today, -13), 14).map((d) => d.rev);
  const weeks = []; for (let i = 11; i >= 0; i--) { const s = startOfWeek(addDays(today, -7 * i)); weeks.push(revenue(inRange(s, addDays(s, 7))).total); }
  const buyers = new Set(state.purchases.map((p) => p.uuid)).size;
  const review = state.purchases.filter(needsReview).length;
  const pstats = playerStats().sort((a, b) => b.total - a.total);
  const rangeDays = state.overview_range;
  const daily = byDay(inRange(addDays(today, -(rangeDays - 1)), addDays(today, 1)), addDays(today, -(rangeDays - 1)), rangeDays);
  const cats = {}; for (const p of state.purchases) { const c = p.category || "Unknown"; cats[c] = (cats[c] || 0) + priceOf(p); }
  const catParts = Object.entries(cats).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({label: k, value: v, color: catColor(k)}));
  const recent = [...state.purchases].slice(-8).reverse();
  const h = state.health, top = h?.top_customer;
  const w2 = cardWidth(2);

  el.innerHTML = `
    <div class="grid kpi">
      ${statTile({label: "Total revenue", value: moneyApprox(all), hero: true, spark: weeks, foot: `${plural(all.count, "purchase")} · ${plural(buyers, "buyer")}`, tip: all.approx ? `Range ${money(all.min)} – ${money(all.max)}` : ""})}
      ${statTile({label: "Today", value: moneyApprox(todayR), delta: deltaOf(todayR.total, yest.total, "yesterday"), foot: plural(todayR.count, "purchase")})}
      ${statTile({label: "Last 7 days", value: moneyApprox(d7), delta: deltaOf(d7.total, p7.total, "prev 7d"), spark: days14.slice(-7), foot: plural(d7.count, "purchase")})}
      ${statTile({label: "Last 30 days", value: moneyApprox(d30), delta: deltaOf(d30.total, p30.total, "prev 30d"), foot: plural(d30.count, "purchase")})}
    </div>
    <div class="grid kpi mt">
      ${statTile({label: "Avg order", value: money(all.count ? all.total / all.count : 0), foot: "all time"})}
      ${statTile({label: "Top spender", value: pstats[0] ? esc(pstats[0].username) : "—", foot: pstats[0] ? `${money(pstats[0].total)} · ${plural(pstats[0].count, "purchase")}` : ""})}
      ${statTile({label: "Store's top customer", value: top ? esc(top.username) : "—", foot: top ? esc(top.text) : "from the store widget"})}
      ${statTile({label: "Needs review", value: review ? `<span style="color:var(--warn)">${review}</span>` : "0", foot: review ? `<a href="#" data-go="feed" data-review="1">ambiguous / unknown prices →</a>` : "all purchases resolved"})}
    </div>

    <div class="section"><h2>Revenue</h2><span class="line"></span>
      <span class="seg" id="ov-range">${[14, 30, 90].map((d) => `<button class="${d === rangeDays ? "active" : ""}" data-d="${d}">${d}d</button>`).join("")}</span></div>
    <div class="grid two">
      <div class="card"><div class="card-head"><h3>Revenue by day</h3><span class="meta">${money(sum(daily, (d) => d.rev))} · ${plural(sum(daily, (d) => d.n), "purchase")}</span></div>
        ${barChart({width: w2, data: daily.map((d) => ({label: fmtDate(d.date), value: d.rev, tip: `<b>${fmtDateY(d.date)}</b><br>${money(d.rev)} · ${d.n} purchase${d.n === 1 ? "" : "s"}`}))})}</div>
      <div class="card"><div class="card-head"><h3>Purchases by day</h3><span class="meta">count</span></div>
        ${barChart({width: w2, data: daily.map((d) => ({label: fmtDate(d.date), value: d.n, tip: `<b>${fmtDateY(d.date)}</b><br>${d.n} purchase${d.n === 1 ? "" : "s"} · ${money(d.rev)}`})), yFmt: (v) => num(Math.round(v)), integer: true})}</div>
    </div>

    <div class="section"><h2>Breakdown</h2><span class="line"></span></div>
    <div class="grid three">
      <div class="card"><h3>By category</h3><div class="desc">all-time revenue share</div>${catParts.length ? stackBar(catParts) : `<div class="empty">No data</div>`}</div>
      <div class="card"><h3>Top players</h3><div class="desc">by total spent</div>
        ${pstats.slice(0, 5).map((s, i) => `<div class="feed"><div class="row click" data-player="${esc(s.uuid)}"><span class="muted" style="width:18px">${i + 1}</span><img class="avatar" src="${avatarUrl(s.uuid)}" alt=""><div class="what"><div class="p"><b>${esc(s.username)}</b></div><div class="t">${plural(s.count, "purchase")} · fav ${esc(s.fav)}</div></div><div class="amt">${s.approx ? "≈" : ""}${money(s.total)}</div></div></div>`).join("") || `<div class="empty">No data</div>`}</div>
      <div class="card"><h3>Recent purchases</h3><div class="desc">latest 8 · <a href="#" data-go="feed">see all</a></div><div class="feed">${recent.map(purchaseRow).join("") || `<div class="empty">Waiting for the first purchase…</div>`}</div></div>
    </div>`;
  $("#ov-range", el).addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) { state.overview_range = +b.dataset.d; renderPage(); } });
}

// ---- feed --------------------------------------------------------------------
function feedFiltered() {
  const f = state.feed, q = state.search.trim().toLowerCase(), now = new Date(), today = startOfDay(now);
  let from = null; if (f.range === "today") from = today; else if (f.range === "7d") from = addDays(today, -6); else if (f.range === "30d") from = addDays(today, -29);
  return state.purchases.filter((p) => {
    if (f.cat !== "all" && (p.category || "Unknown") !== f.cat) return false;
    if (f.source !== "all" && p.source !== f.source) return false;
    if (f.review && !needsReview(p)) return false;
    if (from && p.ts < from) return false;
    if (q && !`${p.username} ${p.product_name} ${p.product_raw} ${p.category}`.toLowerCase().includes(q)) return false;
    return true;
  }).reverse();
}
function renderFeed(el) {
  const f = state.feed, list = feedFiltered(), r = revenue(list);
  const cats = [...new Set(state.purchases.map((p) => p.category || "Unknown"))].sort();
  const pages = Math.max(1, Math.ceil(list.length / f.perPage)); f.page = clamp(f.page, 0, pages - 1);
  const slice = list.slice(f.page * f.perPage, (f.page + 1) * f.perPage);
  el.innerHTML = `
    <div class="filters">
      <span class="seg" data-f="range">${[["all", "All time"], ["today", "Today"], ["7d", "7 days"], ["30d", "30 days"]].map(([k, l]) => `<button class="${f.range === k ? "active" : ""}" data-v="${k}">${l}</button>`).join("")}</span>
      <select class="select" data-f="cat"><option value="all">All categories</option>${cats.map((c) => `<option ${f.cat === c ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>
      <select class="select" data-f="source"><option value="all">Live + seeded</option><option value="live" ${f.source === "live" ? "selected" : ""}>Live only</option><option value="seed" ${f.source === "seed" ? "selected" : ""}>Seeded only</option></select>
      <button class="btn ${f.review ? "primary" : ""}" data-f="review">Needs review</button>
      <span class="grow"></span>
      <span class="total"><b>${num(list.length)}</b> purchase${list.length === 1 ? "" : "s"} · ${moneyApprox(r)}</span>
      <a class="btn" href="/api/export.csv" download>Export CSV</a>
    </div>
    <div class="card" style="padding:6px 6px 12px">
      <div class="table-wrap"><table>
        <thead><tr><th>When</th><th>Player</th><th>Product</th><th>Category</th><th class="r">Price</th><th>Source</th><th></th></tr></thead>
        <tbody>${slice.map((p) => `<tr class="${state.newIds.has(p.id) ? "new" : ""}">
          <td class="nowrap" data-tip="${esc(fmtFull(p.ts))}${p.widget_time ? `<br><span class=k>widget: ${esc(p.widget_time)}</span>` : ""}">${rel(p.ts)}<div class="muted" style="font-size:11px">${fmtDate(p.ts)} ${fmtTime(p.ts)}</div></td>
          <td>${who(p)}</td>
          <td>${esc(p.product_name || p.product_raw)}${p.ambiguous && !p.resolved ? ` <span class="tag warn" data-tip="Widget truncates names; ${p.candidates.length} products match">ambiguous</span>` : ""}${p.price == null ? ` <span class="tag bad">unknown</span>` : ""}${p.resolved ? ` <span class="tag good">resolved</span>` : ""}</td>
          <td>${catTag(p.category)}</td>
          <td class="r"><span class="money ${p.ambiguous && !p.resolved ? "range" : ""}" data-tip="${esc(priceText(p))}">${p.price == null ? "?" : (p.ambiguous && !p.resolved ? "≈" : "") + money(p.price)}</span></td>
          <td><span class="tag ${p.source === "seed" ? "dim" : ""}">${p.source}</span></td>
          <td class="r"><button class="mini-btn" data-resolve="${p.id}">${needsReview(p) ? "resolve" : "edit"}</button></td>
        </tr>`).join("") || `<tr><td colspan="7"><div class="empty">Nothing matches</div></td></tr>`}</tbody>
      </table></div>
      <div class="pager"><span>page ${f.page + 1} / ${pages}</span><button class="mini-btn" data-pg="-1" ${f.page === 0 ? "disabled" : ""}>‹ prev</button><button class="mini-btn" data-pg="1" ${f.page >= pages - 1 ? "disabled" : ""}>next ›</button></div>
    </div>`;
  el.addEventListener("click", (e) => {
    const seg = e.target.closest("[data-f=range] button"); if (seg) { f.range = seg.dataset.v; f.page = 0; renderPage(); return; }
    if (e.target.closest("[data-f=review]")) { f.review = !f.review; f.page = 0; renderPage(); return; }
    const pg = e.target.closest("[data-pg]"); if (pg) { f.page += +pg.dataset.pg; renderPage(); return; }
    const rs = e.target.closest("[data-resolve]"); if (rs) openResolve(+rs.dataset.resolve);
  }, {once: false});
  $$("select[data-f]", el).forEach((s) => s.addEventListener("change", () => { f[s.dataset.f] = s.value; f.page = 0; renderPage(); }));
}

// ---- players -----------------------------------------------------------------
function renderPlayers(el) {
  const q = state.search.trim().toLowerCase();
  let stats = playerStats().filter((s) => !q || s.username.toLowerCase().includes(q) || (state.playerById.get(s.uuid)?.aliases || []).some((a) => a.toLowerCase().includes(q)));
  const sorts = {spent: (a, b) => b.total - a.total, purchases: (a, b) => b.count - a.count, recent: (a, b) => b.last - a.last, name: (a, b) => a.username.localeCompare(b.username)};
  const ranked = playerStats().sort(sorts.spent); const rankOf = new Map(ranked.map((s, i) => [s.uuid, i + 1]));
  stats.sort(sorts[state.players_sort]);
  const total = sum(stats, (s) => s.total);
  el.innerHTML = `
    <div class="filters">
      <span class="seg" id="pl-sort">${[["spent", "Top spenders"], ["purchases", "Most purchases"], ["recent", "Recently active"], ["name", "A–Z"]].map(([k, l]) => `<button class="${state.players_sort === k ? "active" : ""}" data-v="${k}">${l}</button>`).join("")}</span>
      <span class="grow"></span><span class="total"><b>${stats.length}</b> player${stats.length === 1 ? "" : "s"} · ${money(total)}</span>
    </div>
    <div class="grid cards">${stats.map((s) => {
      const rank = rankOf.get(s.uuid), rcls = rank === 1 ? "gold" : rank === 2 ? "silver" : rank === 3 ? "bronze" : "";
      const catsArr = Object.entries(s.cats).sort((a, b) => b[1] - a[1]);
      return `<div class="card pcard click ${rank <= 3 ? "hl" : ""}" data-player="${esc(s.uuid)}">
        <div class="rank ${rcls}">#${rank}</div>
        <div class="top"><img class="avatar lg" src="${avatarUrl(s.uuid, 48)}" alt="" loading="lazy"><div><div class="name">${esc(s.username)}</div><div class="muted" style="font-size:11px">last ${rel(s.last)} · first ${fmtDate(s.first)}</div></div></div>
        <div class="fav">favorite: ${esc(s.fav)}</div>
        <div class="bottom"><div class="spent" data-tip="${s.approx ? esc(`Range ${money(s.min)} – ${money(s.max)}`) : ""}">${s.approx ? "≈" : ""}${money(s.total)}<small>avg ${money(s.avg)}</small></div><span class="pill">${s.count} purchase${s.count === 1 ? "" : "s"}</span></div>
        <div class="catbar" data-tip="${esc(catsArr.map(([k, v]) => `${k}: ${money(v)}`).join(" · "))}">${catsArr.map(([k, v]) => `<span style="flex:${v};background:${catColor(k)}"></span>`).join("")}</div>
      </div>`;
    }).join("") || `<div class="empty" style="grid-column:1/-1">No players yet</div>`}</div>`;
  $("#pl-sort", el).addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) { state.players_sort = b.dataset.v; renderPage(); } });
}

// ---- products ------------------------------------------------------------------
function renderProducts(el) {
  const q = state.search.trim().toLowerCase();
  const {stats, unknown} = productStats();
  const totalRev = sum(stats, (s) => s.revenue) || 1;
  const groups = new Map();
  for (const s of stats) { if (q && !s.product.name.toLowerCase().includes(q)) continue; const c = s.product.category; if (!groups.has(c)) groups.set(c, []); groups.get(c).push(s); }
  const order = [...groups.keys()].sort((a, b) => (CAT_FIXED[a.toLowerCase()] || 9) - (CAT_FIXED[b.toLowerCase()] || 9) || a.localeCompare(b));
  const history = new Map(); for (const h of state.priceHistory || []) { if (!history.has(h.package_id)) history.set(h.package_id, []); history.get(h.package_id).push(h); }
  el.innerHTML = `
    <div class="filters"><span class="total">Prices are re-checked every ${Math.round((state.health?.catalog_interval || 3600) / 60)} min · last ${state.health?.catalog_refreshed_at ? rel(parseTs(state.health.catalog_refreshed_at)) : "—"}</span><span class="grow"></span><button class="btn" id="btn-catalog">Refresh catalog now</button></div>
    ${order.map((cat) => `
      <div class="section"><h2>${esc(cat)}</h2><span class="line"></span><span class="meta">${money(sum(groups.get(cat), (s) => s.revenue))} · ${sum(groups.get(cat), (s) => s.count)} sold</span></div>
      <div class="grid cards">${groups.get(cat).sort((a, b) => b.product.active - a.product.active || (b.product.price || 0) - (a.product.price || 0)).map((s) => {
        const p = s.product, hist = history.get(p.package_id) || [];
        return `<div class="card prod ${p.active ? "" : "inactive"}">
          <div class="top" style="display:flex;gap:12px;align-items:center">
            ${p.image ? `<img class="img" src="${esc(p.image)}" alt="" loading="lazy">` : `<div class="img" style="display:flex;align-items:center;justify-content:center;color:var(--muted)">${esc(p.name[0])}</div>`}
            <div style="min-width:0"><h3 style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(p.name)}</h3>
              <div class="muted" style="font-size:11px">${catTag(p.category)} ${p.type === "subscription" ? `<span class="tag accent">subscription</span>` : ""} ${p.active ? "" : `<span class="tag bad">removed</span>`} ${hist.length > 1 ? `<span class="tag warn" data-tip="${esc(hist.map((h) => `${fmtDateY(parseTs(h.seen_at))}: ${money(h.price)}`).join("<br>"))}">price changed ×${hist.length - 1}</span>` : ""}</div></div>
          </div>
          <div class="price mt">${p.original_price != null && p.original_price !== p.price ? `<s>${money(p.original_price)}</s>` : ""}${money(p.price)}</div>
          <div class="stats"><div><div class="k">Sold</div><div class="v">${s.count}${s.possible ? ` <span class="muted" data-tip="ambiguous purchases that might be this product">+${s.possible}?</span>` : ""}</div></div><div><div class="k">Revenue</div><div class="v">${money(s.revenue)}</div></div><div><div class="k">Last sold</div><div class="v">${s.last ? rel(s.last) : "—"}</div></div></div>
          <div class="sharebar" data-tip="${esc(`${pct(s.revenue / totalRev)} of tracked revenue`)}"><span style="width:${(100 * s.revenue / totalRev).toFixed(1)}%"></span></div>
          ${productUrl(p) ? `<div class="muted mt" style="font-size:11px"><a href="${esc(productUrl(p))}" target="_blank" rel="noopener">#${p.package_id} on store ↗</a></div>` : ""}
        </div>`;
      }).join("")}</div>`).join("")}
    ${unknown.length ? `<div class="section"><h2>Unmatched</h2><span class="line"></span></div><div class="card"><div class="desc">Widget names that matched no product in the catalog. Resolve them from the Live Feed.</div>${[...new Set(unknown.map((p) => p.product_raw))].map((n) => `<span class="tag bad" style="margin:3px">${esc(n)} ×${unknown.filter((p) => p.product_raw === n).length}</span>`).join("")}</div>` : ""}`;
  $("#btn-catalog", el).addEventListener("click", async (e) => { e.target.disabled = true; try { await api("/api/catalog/refresh", {method: "POST"}); toast("Catalog refresh queued", "takes ~10 seconds", true); setTimeout(tickProducts, 15000); } catch { toast("Failed to queue refresh", "", true); } });
}

// ---- analytics ---------------------------------------------------------------------
function renderAnalytics(el) {
  const today = startOfDay(new Date()), days = state.analytics_range;
  const from = days === 0 ? (state.purchases[0]?.ts ? startOfDay(state.purchases[0].ts) : today) : addDays(today, -(days - 1));
  const list = state.purchases.filter((p) => p.ts >= from);
  const w2 = cardWidth(2), w1 = contentWidth() - 42;
  // cumulative
  const nDays = Math.max(1, Math.round((addDays(today, 1) - from) / DAY));
  const daily = byDay(list, from, nDays); let acc = 0;
  const cum = daily.map((d) => { acc += d.rev; return {label: fmtDate(d.date), value: acc, tip: `<b>${fmtDateY(d.date)}</b><br>cumulative ${money(acc)}<br><span class=k>${money(d.rev)} that day</span>`}; });
  // weekly by category (stacked)
  const cats = [...new Set(state.purchases.map((p) => p.category || "Unknown"))].sort((a, b) => (CAT_FIXED[a.toLowerCase()] || 9) - (CAT_FIXED[b.toLowerCase()] || 9));
  const series = cats.map((c) => ({key: c, label: c, color: catColor(c)}));
  const weekMap = new Map(); const wk0 = startOfWeek(from); for (let d = wk0; d <= today; d = addDays(d, 7)) weekMap.set(dayKey(d), {label: fmtDate(d), values: {}});
  for (const p of list) { const k = dayKey(startOfWeek(p.ts)); const b = weekMap.get(k); if (b) { const c = p.category || "Unknown"; b.values[c] = (b.values[c] || 0) + priceOf(p); } }
  // new vs returning per week
  const firstSeen = new Map(); for (const p of state.purchases) if (!firstSeen.has(p.uuid)) firstSeen.set(p.uuid, p.ts);
  const retMap = new Map(); for (const [k, v] of weekMap) retMap.set(k, {label: v.label, values: {new: 0, returning: 0}});
  for (const p of list) { const k = dayKey(startOfWeek(p.ts)); const b = retMap.get(k); if (b) { b.values[firstSeen.get(p.uuid) === p.ts ? "new" : "returning"]++; } }
  // heatmap (local time)
  const grid = Array.from({length: 7}, () => Array(24).fill(0));
  for (const p of list) grid[(p.ts.getDay() + 6) % 7][p.ts.getHours()] += priceOf(p);
  // top products
  const prodAgg = new Map(); for (const p of list) { const n = p.product_name || p.product_raw; const a = prodAgg.get(n) || {value: 0, count: 0, category: p.category}; a.value += priceOf(p); a.count++; a.category = a.category || p.category; prodAgg.set(n, a); }
  const topProds = [...prodAgg.entries()].sort((a, b) => b[1].value - a[1].value).slice(0, 10).map(([label, a]) => ({label, value: a.value, extra: `×${a.count}`, color: catColor(a.category), tip: `<b>${esc(label)}</b><br>${money(a.value)} · ${a.count} sold`}));
  // order value distribution
  const buckets = [[0, 5], [5, 10], [10, 20], [20, 50], [50, 100], [100, Infinity]];
  const dist = buckets.map(([a, b]) => ({label: b === Infinity ? `${money(a).replace(/\.00$/, "")}+` : `${money(a).replace(/\.00$/, "")}–${money(b).replace(/\.00$/, "")}`, value: list.filter((p) => priceOf(p) >= a && priceOf(p) < b).length}));
  dist.forEach((d) => d.tip = `<b>${d.label}</b><br>${d.value} purchases`);
  // hours & players summary
  const hourTotals = Array(24).fill(0); grid.forEach((r) => r.forEach((v, i) => hourTotals[i] += v));
  const peakHour = hourTotals.indexOf(Math.max(...hourTotals));
  const dayTotals = grid.map((r) => sum(r)); const peakDay = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][dayTotals.indexOf(Math.max(...dayTotals))];
  const buyers = new Set(list.map((p) => p.uuid)).size, repeat = [...new Set(list.map((p) => p.uuid))].filter((u) => list.filter((p) => p.uuid === u).length > 1).length;

  el.innerHTML = `
    <div class="filters"><span class="seg" id="an-range">${[[30, "30 days"], [90, "90 days"], [365, "1 year"], [0, "All time"]].map(([d, l]) => `<button class="${d === days ? "active" : ""}" data-d="${d}">${l}</button>`).join("")}</span>
      <span class="grow"></span><span class="total"><b>${num(list.length)}</b> purchase${list.length === 1 ? "" : "s"} · ${money(revenue(list).total)} · since ${fmtDateY(from)}</span></div>
    <div class="grid kpi">
      ${statTile({label: "Peak hour", value: list.length ? `${String(peakHour).padStart(2, "0")}:00` : "—", foot: "local time, by revenue"})}
      ${statTile({label: "Peak weekday", value: list.length ? peakDay : "—", foot: "by revenue"})}
      ${statTile({label: "Repeat buyers", value: buyers ? `${repeat} <span class="approx">/ ${buyers}</span>` : "—", foot: buyers ? `${pct(repeat / buyers)} bought more than once` : ""})}
      ${statTile({label: "Revenue / buyer", value: money(buyers ? revenue(list).total / buyers : 0), foot: "in range"})}
    </div>
    <div class="section"><h2>Growth</h2><span class="line"></span></div>
    <div class="grid two">
      <div class="card"><div class="card-head"><h3>Cumulative revenue</h3><span class="meta">running total</span></div>${areaChart({width: w2, points: cum})}</div>
      <div class="card"><div class="card-head"><h3>Weekly revenue by category</h3><span class="meta">weeks starting Monday</span></div>${stackedChart({width: w2, data: [...weekMap.values()], series})}${legend(series)}</div>
    </div>
    <div class="section"><h2>Patterns</h2><span class="line"></span></div>
    <div class="grid two">
      <div class="card wide"><div class="card-head"><h3>When people buy</h3><span class="meta">revenue by weekday × hour (your local time)</span></div>${heatmap({width: w1, grid, rowLabels: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], colLabels: Array.from({length: 24}, (_, i) => String(i).padStart(2, "0")), fmt: money})}</div>
      <div class="card"><div class="card-head"><h3>Top products</h3><span class="meta">by revenue</span></div>${topProds.length ? hbarChart({width: w2, rows: topProds}) : `<div class="empty">No data</div>`}${legend(series)}</div>
      <div class="card"><div class="card-head"><h3>Order size</h3><span class="meta">purchases per price band</span></div>${barChart({width: w2, data: dist, yFmt: (v) => num(Math.round(v)), integer: true})}</div>
      <div class="card wide"><div class="card-head"><h3>New vs returning buyers</h3><span class="meta">purchases per week</span></div>${stackedChart({width: w1, data: [...retMap.values()], series: [{key: "new", label: "First purchase", color: "var(--accent)"}, {key: "returning", label: "Returning", color: "var(--border-2)"}], yFmt: (v) => num(Math.round(v)), integer: true})}${legend([{label: "First purchase", color: "var(--accent)"}, {label: "Returning", color: "var(--border-2)"}])}</div>
    </div>`;
  $("#an-range", el).addEventListener("click", (e) => { const b = e.target.closest("button"); if (b) { state.analytics_range = +b.dataset.d; renderPage(); } });
}

// ---- status ----------------------------------------------------------------------
function renderStatus(el) {
  const h = state.health; if (!h) { el.innerHTML = `<div class="empty">No health data</div>`; return; }
  const last = h.last_poll, lastOk = h.last_ok, w2 = cardWidth(2);
  const polls = (h.recent_polls || []).map((p) => ({label: fmtTime(parseTs(p.at)), value: p.ok ? (p.latency_ms || 0) : 0, tip: `<b>${fmtFull(parseTs(p.at))}</b><br>${p.ok ? `${p.latency_ms} ms · ${p.new_count} new` : "failed"}`}));
  const words = {ok: "Healthy", degraded: "Degraded", blocked: "Blocked by Cloudflare", starting: "Starting"};
  const subs = {ok: "polling normally", degraded: `${h.consecutive_failures} consecutive failure(s), backing off`, blocked: "TLS profile rotation in progress — see logs", starting: "waiting for the first poll"};
  el.innerHTML = `
    <div class="grid two">
      <div class="card ${h.status === "ok" ? "hl" : ""}">
        <div class="status-big ${h.status}"><span class="dotbig"></span><div><div class="word">${words[h.status] || h.status}</div><div class="sub">${subs[h.status] || ""}${h.last_error ? ` · ${esc(h.last_error)}` : ""}${!h.last_error && h.last_failure ? ` · last failure ${rel(parseTs(h.last_failure.at))}: ${esc(h.last_failure.error)}` : ""}</div></div></div>
        <div class="kv mt">
          <span class="k">Last poll</span><span class="v">${last ? `${rel(parseTs(last.at))} · ${last.ok ? `${last.latency_ms} ms` : `<span style="color:var(--bad)">failed</span>`}` : "—"}</span>
          <span class="k">Last success</span><span class="v">${lastOk ? rel(parseTs(lastOk.at)) : "—"}</span>
          <span class="k">Success rate</span><span class="v">${h.success_rate != null ? h.success_rate + "%" : "—"} <span class="muted">(last 500 polls)</span></span>
          <span class="k">Avg latency</span><span class="v">${h.avg_latency_ms != null ? h.avg_latency_ms + " ms" : "—"}</span>
          <span class="k">Poll interval</span><span class="v">${h.poll_interval}s · catalog every ${Math.round(h.catalog_interval / 60)} min</span>
          <span class="k">TLS profile</span><span class="v">${esc(h.profile)}</span>
          <span class="k">Uptime</span><span class="v">${fmtDur(h.uptime_s)} · since ${fmtFull(parseTs(h.started_at))}</span>
          <span class="k">Version</span><span class="v">StoreScope ${esc(h.version)}</span>
          <span class="k">Database</span><span class="v">${bytes(h.db_size)} · ${num(h.counts.purchases)} purchases · ${num(h.counts.players)} players · ${num(h.counts.products)} products</span>
          <span class="k">Widget clock</span><span class="v">UTC${h.widget_tz_offset_min >= 0 ? "+" : "−"}${Math.abs(h.widget_tz_offset_min / 60)}h <span class="muted">(${esc(h.widget_tz_source)}${h.widget_clock_samples ? ` · ${plural(h.widget_clock_samples, "sample")}, median lag ${h.widget_clock_offset_min} min` : ""})</span></span>
          <span class="k">Discord</span><span class="v">${h.discord ? `<span class="tag good">webhook on</span>` : `<span class="tag dim">off</span>`}</span>
          <span class="k">Ambiguous prices</span><span class="v">policy: ${esc(h.ambiguous_policy)} · seed existing: ${h.seed_existing ? "yes" : "no"}</span>
          <span class="k">Checkout sale</span><span class="v">${h.checkout_discount_percent > 0 ? `${h.checkout_discount_percent}% off${h.checkout_discount_min_basket > 0 ? ` baskets ${money(h.checkout_discount_min_basket)}+` : ""} <span class="tag warn">estimated</span>` : `<span class="tag dim">off</span>`}</span>
        </div>
        <div class="filters mt"><button class="btn" id="st-catalog">Refresh catalog</button><a class="btn" href="/api/export.csv" download>Export CSV</a><a class="btn" href="/api/docs" target="_blank">API docs</a></div>
      </div>
      <div class="card"><div class="card-head"><h3>Poll latency</h3><span class="meta">last ${polls.length} polls · zero = failed</span></div>${polls.length ? barChart({width: w2, height: 180, data: polls, yFmt: (v) => Math.round(v) + "ms", integer: true}) : `<div class="empty">No polls yet</div>`}
        <h3 class="mt">Widget snapshot</h3><div class="desc">what the store showed on the last poll</div>
        ${(h.snapshot?.strict || []).map((k) => { const [uuid, prod, hhmm] = k.split("|"); const pl = state.playerById.get(uuid); return `<div class="feed"><div class="row"><img class="avatar" src="${avatarUrl(uuid)}" alt=""><div class="what"><div class="p"><b>${esc(pl?.username || uuid.slice(0, 8))}</b> · ${esc(prod)}</div></div><span class="muted">${esc(hhmm)}</span></div></div>`; }).join("") || `<div class="muted">—</div>`}
      </div>
    </div>
    <div class="section"><h2>Ignored players</h2><span class="line"></span><span class="meta">never recorded · set from a player's detail view or IGNORED_PLAYERS in .env</span></div>
    <div class="card">${(h.ignored_players || []).length ? `<div class="feed">${h.ignored_players.map((e) => `<div class="row"><img class="avatar" src="${avatarUrl(e.uuid || ("name:" + (e.username || "")))}" alt=""><div class="what"><div class="p"><b>${esc(e.username || e.uuid)}</b></div><div class="t">${e.uuid ? esc(e.uuid) : "matched by name only"}${e.source === "env" ? " · from .env" : e.added_at ? ` · since ${fmtDateY(parseTs(e.added_at))}` : ""}</div></div>${e.source === "env" ? `<span class="tag dim">env</span>` : `<button class="mini-btn" data-unignore="${esc(e.uuid || e.username)}" data-label="${esc(e.username || e.uuid)}">unignore</button>`}</div>`).join("")}</div>` : `<div class="muted">Nobody is ignored. Open a player and hit “Ignore player” to exclude your own test purchases.</div>`}</div>
    <div class="section"><h2>Events</h2><span class="line"></span></div>
    <div class="grid two top">
      <div class="card events">${state.events.length ? state.events.slice(0, 40).map((e) => `<div class="ev"><span class="t" data-tip="${esc(fmtFull(parseTs(e.at)))}">${rel(parseTs(e.at))}</span><span class="tag ${/block|fail|unknown|anomaly|removed/.test(e.type) ? "bad" : /price|top|username/.test(e.type) ? "warn" : /new|recover|resolved/.test(e.type) ? "good" : ""}">${esc(e.type)}</span><span>${esc(e.message)}</span></div>`).join("") : `<div class="empty">No events yet</div>`}</div>
      <div class="card"><div class="card-head"><h3>Log</h3><span class="meta">last ${state.logs.length} lines</span></div><div class="log" id="logbox">${state.logs.map((l) => `<div class="l ${esc(l.level)}"><span class="t">${fmtTime(parseTs(l.at))}</span><span>${esc(l.message)}</span></div>`).join("") || "no log lines yet"}</div></div>
    </div>`;
  const lb = $("#logbox", el); if (lb) lb.scrollTop = lb.scrollHeight;
  el.addEventListener("click", (e) => { const b = e.target.closest("[data-unignore]"); if (b) unignorePlayer(b.dataset.unignore, b.dataset.label); });
  $("#st-catalog", el).addEventListener("click", async (e) => { e.target.disabled = true; try { await api("/api/catalog/refresh", {method: "POST"}); toast("Catalog refresh queued", "", true); } catch { toast("Failed", "", true); } });
}

// ---------------------------------------------------------------------------
// modals
// ---------------------------------------------------------------------------
function openModal(html) { $("#modal").onclick = null; $("#modal").innerHTML = html + `<button class="mini-btn close" id="modal-close">✕</button>`; $("#modal-bg").classList.add("open"); $("#modal-close").onclick = closeModal; }
function closeModal() { $("#modal-bg").classList.remove("open"); }

function openPlayer(uuid) {
  const s = playerStats().find((x) => x.uuid === uuid); if (!s) return;
  const pl = state.playerById.get(uuid) || {}, list = state.purchases.filter((p) => p.uuid === uuid).reverse();
  const months = new Map(); for (const p of list) { const k = monthKey(p.ts); months.set(k, (months.get(k) || 0) + priceOf(p)); }
  const mData = [...months.entries()].sort().slice(-12).map(([k, v]) => ({label: k.slice(2), value: v, tip: `<b>${k}</b><br>${money(v)}`}));
  const prods = Object.entries(s.prods).sort((a, b) => b[1].amount - a[1].amount).map(([label, a]) => ({label, value: a.amount, extra: `×${a.count}`, color: catColor(a.category), tip: `${label}: ${money(a.amount)} · ${a.count}`}));
  const rank = playerStats().sort((a, b) => b.total - a.total).findIndex((x) => x.uuid === uuid) + 1;
  const w = Math.min(880, window.innerWidth - 80) - 52, half = Math.floor(w / 2) - 30;
  openModal(`
    <div class="modal-head"><img class="avatar xl" src="${bustUrl(uuid)}" alt="" style="width:72px;height:auto;image-rendering:auto"><div><h2>${esc(s.username)} <span class="muted" style="font-size:16px">#${rank}</span></h2>
      <div class="sub">${pl.aliases?.length ? `also known as ${pl.aliases.map(esc).join(", ")} · ` : ""}first seen ${fmtDateY(s.first)} · last ${rel(s.last)} · ${s.days.size} active day${s.days.size === 1 ? "" : "s"}</div>
      <div class="sub" style="font-size:10px">${uuid.startsWith("name:") ? "no uuid (name only)" : esc(uuid)}</div></div></div>
    <div class="grid kpi">
      ${statTile({label: "Total spent", value: `${s.approx ? '<span class="approx">≈</span>' : ""}${money(s.total)}`, hero: true, tip: s.approx ? `Range ${money(s.min)} – ${money(s.max)}` : ""})}
      ${statTile({label: "Purchases", value: num(s.count), foot: `avg ${money(s.avg)}`})}
      ${statTile({label: "Favorite", value: `<span style="font-size:15px;line-height:1.3;display:block">${esc(s.fav)}</span>`, foot: `bought ${s.prods[s.fav]?.count || 0}×`})}
      ${statTile({label: "Share of revenue", value: pct(s.total / (revenue(state.purchases).total || 1)), foot: "of all tracked"})}
    </div>
    <div class="filters mt" style="margin-bottom:0"><span class="grow"></span><button class="btn danger" id="pl-ignore" data-tip="Delete this player's purchases and never record them again (your own test buys, staff)">Ignore player</button></div>
    <div class="grid two mt">
      <div class="card"><div class="card-head"><h3>Spend by month</h3></div>${mData.length ? barChart({width: half, height: 170, data: mData}) : `<div class="empty">—</div>`}</div>
      <div class="card"><div class="card-head"><h3>By product</h3></div>${hbarChart({width: half, rows: prods})}</div>
    </div>
    <div class="card mt" style="padding:6px 6px 10px"><div class="table-wrap"><table><thead><tr><th>When</th><th>Product</th><th>Category</th><th class="r">Price</th></tr></thead>
      <tbody>${list.map((p) => `<tr><td class="nowrap">${fmtDateY(p.ts)} ${fmtTime(p.ts)}</td><td>${esc(p.product_name || p.product_raw)}${p.ambiguous && !p.resolved ? ` <span class="tag warn">ambiguous</span>` : ""}</td><td>${catTag(p.category)}</td><td class="r money">${p.price == null ? "?" : (p.ambiguous && !p.resolved ? "≈" : "") + money(p.price)}</td></tr>`).join("")}</tbody></table></div></div>`);
  $("#pl-ignore").onclick = () => ignorePlayer(uuid, s.username, list.length);
}

async function ignorePlayer(uuid, username, count) {
  if (!confirm(`Ignore ${username}?\n\nThis deletes their ${count} purchase${count === 1 ? "" : "s"} and skips anything they buy from now on.`)) return;
  try {
    const r = await api("/api/ignored", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({uuid, username, purge: true})});
    state.purchases = state.purchases.filter((p) => p.uuid !== uuid);
    state.purchaseById = new Map(state.purchases.map((p) => [p.id, p]));
    const pl = await api("/api/players"); indexPlayers(pl.players);
    state.health = await api("/api/health");
    closeModal(); toast(`${username} ignored`, `${r.removed} purchase${r.removed === 1 ? "" : "s"} removed`, true); renderPage(); updatePill();
  } catch (e) { toast("Failed: " + e.message, "", true); }
}
async function unignorePlayer(key, label) {
  try { await api(`/api/ignored/${encodeURIComponent(key)}`, {method: "DELETE"}); state.health = await api("/api/health"); toast(`${label} no longer ignored`, "future purchases will be recorded", true); renderPage(); }
  catch (e) { toast("Failed: " + e.message, "", true); }
}

function openResolve(id) {
  const p = state.purchaseById.get(id); if (!p) return;
  const cands = (p.candidates || []).map((c) => state.productById.get(c)).filter(Boolean);
  const byCat = new Map(); for (const pr of state.products) { if (!byCat.has(pr.category)) byCat.set(pr.category, []); byCat.get(pr.category).push(pr); }
  openModal(`
    <div class="modal-head"><img class="avatar lg" src="${avatarUrl(p.uuid, 48)}" alt=""><div><h2>Resolve purchase #${p.id}</h2><div class="sub">${esc(p.username)} · widget showed “${esc(p.product_raw)}” · ${fmtFull(p.ts)}</div></div></div>
    ${cands.length ? `<div class="desc">The store widget truncates product names to 20 characters, so this could be any of these:</div>
      <div class="grid cards">${cands.map((c) => `<div class="card click" data-pick="${c.package_id}"><h3>${esc(c.name)}</h3><div class="muted">${catTag(c.category)}</div><div class="price mt" style="font-size:20px;font-weight:600">${money(c.price)}</div></div>`).join("")}</div>` : `<div class="desc">This name did not match any product in the catalog.</div>`}
    <div class="section"><h2>Or</h2><span class="line"></span></div>
    <div class="filters">
      <select class="select" id="rs-product"><option value="">Pick any product…</option>${[...byCat.entries()].map(([cat, list]) => `<optgroup label="${esc(cat)}">${list.map((c) => `<option value="${c.package_id}">${esc(c.name)} — ${money(c.price)}</option>`).join("")}</optgroup>`).join("")}</select>
      <input class="select" id="rs-price" type="number" step="0.01" min="0" placeholder="custom price" style="width:140px">
      <button class="btn primary" id="rs-save">Save</button>
      <span class="grow"></span>
      <button class="btn" id="rs-reset" data-tip="Back to automatic matching">Reset to auto</button>
      <button class="btn danger" id="rs-delete" data-tip="Remove this purchase (false positive)">Delete</button>
    </div>
    <div class="muted" style="font-size:11px">Current: ${esc(p.product_name || p.product_raw)} · ${esc(priceText(p))}${p.resolved ? " · manually resolved" : ""}</div>`);
  const save = async (body) => { try { await api(`/api/purchases/${id}/resolve`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)}); await refreshPurchase(id); closeModal(); toast("Purchase updated", "", true); renderPage(); } catch (e) { toast("Failed: " + e.message, "", true); } };
  $("#modal").onclick = (e) => { const c = e.target.closest("[data-pick]"); if (c) save({package_id: +c.dataset.pick}); };
  $("#rs-save").onclick = () => { const pid = $("#rs-product").value, pr = $("#rs-price").value; if (!pid && pr === "") return toast("Pick a product or enter a price", "", true); save({package_id: pid ? +pid : null, price: pr === "" ? null : +pr}); };
  $("#rs-reset").onclick = () => save({reset: true});
  $("#rs-delete").onclick = async () => { if (!confirm(`Delete purchase #${id} (${p.username} · ${p.product_raw})?`)) return; try { await api(`/api/purchases/${id}`, {method: "DELETE"}); state.purchases = state.purchases.filter((x) => x.id !== id); state.purchaseById.delete(id); closeModal(); toast("Purchase deleted", "", true); renderPage(); updatePill(); } catch (e) { toast("Failed: " + e.message, "", true); } };
}
async function refreshPurchase(id) {
  const r = await api(`/api/purchases?since_id=${id - 1}&limit=1`);
  if (r.purchases[0] && r.purchases[0].id === id) addPurchases(r.purchases);
}

// ---------------------------------------------------------------------------
// toast + tooltip + navigation
// ---------------------------------------------------------------------------
function toast(t1, t2 = "", plain = false) {
  const el = document.createElement("div"); el.className = "toast" + (plain ? " plain" : "");
  el.innerHTML = `<div><div class="t1">${esc(t1)}</div>${t2 ? `<div class="t2">${esc(t2)}</div>` : ""}</div>`;
  $("#toasts").appendChild(el); setTimeout(() => el.remove(), 6000);
}
function initTooltip() {
  const tip = $("#tip"); let cur = null;
  document.addEventListener("mouseover", (e) => { const t = e.target.closest("[data-tip]"); if (t && t.dataset.tip) { cur = t; tip.innerHTML = t.dataset.tip; tip.style.display = "block"; } });
  document.addEventListener("mouseout", (e) => { if (cur && !cur.contains(e.relatedTarget)) { cur = null; tip.style.display = "none"; } });
  document.addEventListener("mousemove", (e) => { if (!cur) return; const x = Math.min(e.clientX + 14, window.innerWidth - tip.offsetWidth - 10), y = e.clientY + 16 + tip.offsetHeight > window.innerHeight ? e.clientY - tip.offsetHeight - 10 : e.clientY + 16; tip.style.left = x + "px"; tip.style.top = y + "px"; });
}
const RENDER = {overview: renderOverview, feed: renderFeed, players: renderPlayers, products: renderProducts, analytics: renderAnalytics, status: renderStatus};
function renderPage() {
  const el = $(`#page-${state.page}`); if (!el) return;
  const scroll = $("#content").scrollTop;
  const fresh = el.cloneNode(false); el.replaceWith(fresh);
  RENDER[state.page](fresh);
  $("#page-title").textContent = PAGES[state.page].title; $("#page-sub").textContent = PAGES[state.page].sub();
  $("#content").scrollTop = scroll;
}
function go(page) {
  state.page = page;
  $$("#nav button").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
  $$(".page").forEach((p) => p.classList.toggle("active", p.id === `page-${page}`));
  if (page === "status") api("/api/logs?limit=200").then((r) => { state.logs = r.logs; renderPage(); }).catch(() => {});
  if (page === "products" && !state.priceHistory) tickProducts();
  renderPage(); $("#content").scrollTop = 0;
  try { location.hash = page; } catch {}
}

document.addEventListener("click", (e) => {
  const nav = e.target.closest("#nav button"); if (nav) return go(nav.dataset.page);
  const g = e.target.closest("[data-go]"); if (g) { e.preventDefault(); if (g.dataset.review) { state.feed.review = true; state.feed.range = "all"; } return go(g.dataset.go); }
  const pl = e.target.closest("[data-player]"); if (pl) return openPlayer(pl.dataset.player);
  if (e.target.id === "modal-bg") closeModal();
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); if (e.key === "/" && document.activeElement !== $("#search")) { e.preventDefault(); $("#search").focus(); } });
let searchTimer = null;
$("#search").addEventListener("input", (e) => { state.search = e.target.value; clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.feed.page = 0; renderPage(); }, 120); });
let resizeTimer = null;
window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(renderPage, 150); });

async function init() {
  initTheme(); initTooltip();
  try {
    await loadAll();
    $("#loading").classList.add("hidden");
    const hash = location.hash.replace("#", ""); go(PAGES[hash] ? hash : "overview");
    updatePill();
    setInterval(tick, 10000); setInterval(tickHealth, 30000); setInterval(tickProducts, 600000);
    setInterval(() => { if (state.page === "overview" || state.page === "feed") renderPage(); }, 60000); // keep relative times fresh
  } catch (e) {
    $("#loading").innerHTML = `Could not load data: ${esc(e.message)}<br><span style="font-size:12px">Is the tracker running? Retrying…</span>`;
    setTimeout(init, 5000);
  }
}
if (document.fonts?.ready) document.fonts.ready.then(() => { if (state.online) renderPage(); });
init();
})();
