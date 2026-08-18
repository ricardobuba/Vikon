// Vikon — frontend mobile-first. Consume la API JSON; el motor decide, aquí solo pintamos.
const $ = (s) => document.querySelector(s);
const api = (p, opts) => fetch(p, opts).then((r) => r.ok ? r.json() : r.json().then((e) => Promise.reject(e)));
const fmt = (v, d = 0) => (v == null ? "—" : Number(v).toFixed(d));
// Versión de los textos legales que el usuario acepta al registrarse. Se guarda
// junto a la fecha: sin esto no se puede demostrar A QUÉ TEXTO dijo que sí.
// Subirla al cambiar privacidad.html o terminos.html de forma sustancial.
const LEGAL_VERSION = "1.0";
const signed = (v) => (v == null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(0));
const shortDate = (iso) => new Date(iso).toLocaleDateString("es-ES", { day: "numeric", month: "short" });
// Duración en H:MM (formato único en toda la app). 95 → "1:35", 45 → "0:45".
const hhmm = (min) => {
  if (min == null) return "—";
  const t = Math.round(Number(min));
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
};

// --- Iconos SVG minimalistas (línea, heredan el color del texto) -------------
const ICON = {
  home: '<path d="M3 10.8 12 3.5l9 7.3"/><path d="M5.5 9.5V20h13V9.5"/>',
  chart: '<path d="M5 20V11M12 20V4.5M19 20v-6.5"/>',
  calendar: '<rect x="4" y="5" width="16" height="15" rx="2.2"/><path d="M4 9.5h16M8.5 3v4M15.5 3v4"/>',
  chat: '<path d="M20 14.5a2 2 0 0 1-2 2H8l-4 3.5V6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2z"/>',
  settings: '<path d="M3.5 7.5h9M17 7.5h3.5M3.5 16.5h3M11 16.5h9.5"/><circle cx="14.5" cy="7.5" r="2.1"/><circle cx="7.5" cy="16.5" r="2.1"/>',
  bike: '<circle cx="5.5" cy="16.5" r="3.6"/><circle cx="18.5" cy="16.5" r="3.6"/>'
      + '<path d="M12 16.5 9.2 9.6 15.4 8.2 12 16.5M12 16.5H5.5M15.4 8.2 18.5 16.5"/>'
      + '<path d="M8 9.6h2.6M14 6.9h2.8"/>',
  pulse: '<path d="M3 12h4l2.4 6 4-13 2.3 7H21"/>',
  battery: '<rect x="3" y="8.5" width="15" height="8" rx="1.6"/><path d="M21 11v3"/><path d="M6.5 11v3"/>',
  pencil: '<path d="M4 20l1-4L15.5 5.5 19 9 8 20z"/><path d="M13.5 7.5 17 11"/>',
  target: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3.6"/><circle cx="12" cy="12" r="0.6" fill="currentColor" stroke="none"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7"/>',
  refresh: '<path d="M4.5 11a7.5 7.5 0 0 1 12.7-4.2L20 9"/><path d="M20 4.2V9h-4.8"/><path d="M19.5 13a7.5 7.5 0 0 1-12.7 4.2L4 15"/><path d="M4 19.8V15h4.8"/>',
  send: '<path d="M4.5 12 20 5l-6.5 15-2.6-6.2z"/><path d="M11 13.5 20 5"/>',
  chevron: '<path d="M9 6l6 6-6 6"/>',
  flame: '<path d="M12 3c1 3-2 4-2 7a2 2 0 0 0 4 0c2 2 3 3.5 3 6a5 5 0 0 1-10 0c0-3 2-4 3-6 .8 1 2 1.6 2 3"/>',
};
function icon(name, size = 20) {
  return `<svg class="ic" viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" `
    + `stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${ICON[name] || ""}</svg>`;
}

// Tipos de evento objetivo (características que usa la planificación por fases).
const EVENT_KINDS = [
  ["", "Tipo de evento…"], ["gran_fondo", "Gran fondo"], ["ruta", "Carrera en ruta"],
  ["crono", "Contrarreloj"], ["criterium", "Criterium"], ["mtb", "MTB / Maratón"], ["otro", "Otro"],
];
const kindOptions = (sel) => EVENT_KINDS.map(([v, l]) =>
  `<option value="${v}"${v === sel ? " selected" : ""}>${l}</option>`).join("");
function paintIcons() {
  document.querySelectorAll("nav button").forEach((b) => {
    const s = b.querySelector(".ico"); if (s) s.innerHTML = icon(b.dataset.icon, 21);
  });
  document.querySelectorAll("#quick button").forEach((b) => {
    const s = b.querySelector(".ci"); if (s) s.innerHTML = icon(b.dataset.icon, 16);
  });
  const send = $("#chat-send"); if (send && !send.querySelector("svg")) send.innerHTML = icon("send", 20);
}

// --- Utilidades de gráficas SVG (sin dependencias) --------------------------
// Marcas "redondas" para un eje entre min y max (0, 50, 100…).
function niceTicks(min, max, count = 4) {
  const span = (max - min) || 1;
  const raw = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
  const ticks = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) ticks.push(v);
  return ticks;
}

const legend = (items) => `<div class="legend">${items.map((i) =>
  `<span><i class="dot" style="background:${i.color}"></i>${i.label}</span>`).join("")}</div>`;

// Etiquetas de fecha por punto (con año si la serie cruza años).
function labelDates(isoArr) {
  if (!isoArr.length) return [];
  const spanY = new Date(isoArr[0]).getFullYear() !== new Date(isoArr[isoArr.length - 1]).getFullYear();
  return isoArr.map((iso) => spanY
    ? new Date(iso).toLocaleDateString("es-ES", { month: "short", year: "2-digit" })
    : shortDate(iso));
}

// Curva de potencia: eje X logarítmico (5 s → 1 h), real vs modelo CP/W'.
function fmtDur(s) { return s >= 60 ? (s % 60 === 0 ? s / 60 + " min" : (s / 60).toFixed(1) + " min") : s + " s"; }

// Curva de potencia INTERACTIVA: eje X log (5 s→1 h), real vs modelo, tooltip.
function mountPowerChart(el, points, cp) {
  const W = 344, H = 198;
  const mL = 40, mR = 12, mT = 10, mB = 28;
  const iw = W - mL - mR, ih = H - mT - mB;
  const lx = (s) => Math.log10(s);
  const secs = points.map((p) => p.seconds);
  const xmin = lx(Math.min(...secs)), xmax = lx(Math.max(...secs));
  const X = (s) => mL + (lx(s) - xmin) / (xmax - xmin) * iw;
  const yv = points.flatMap((p) => [p.actual, p.predicted]).filter((v) => v != null);
  if (!yv.length) { el.innerHTML = `<div class="sub">sin datos</div>`; return; }
  const ymax = Math.max(...yv) * 1.05;
  const Y = (v) => mT + (1 - v / ymax) * ih;

  let grid = niceTicks(0, ymax, 4).map((v) => {
    const y = Y(v).toFixed(1);
    return `<line x1="${mL}" y1="${y}" x2="${W - mR}" y2="${y}" stroke="var(--grid)"/>`
      + `<text x="${mL - 5}" y="${(+y + 3).toFixed(1)}" fill="var(--muted)" font-size="9" text-anchor="end">${Math.round(v)}</text>`;
  }).join("");
  if (cp) {
    const y = Y(cp).toFixed(1);
    grid += `<line x1="${mL}" y1="${y}" x2="${W - mR}" y2="${y}" stroke="#039C86" stroke-width="1" stroke-dasharray="2 3" opacity=".7"/>`
      + `<text x="${W - mR}" y="${(+y - 4).toFixed(1)}" fill="#039C86" font-size="9" text-anchor="end">CP ${Math.round(cp)}W</text>`;
  }
  const ticksX = [[5, "5s"], [30, "30s"], [60, "1m"], [300, "5m"], [1200, "20m"], [3600, "1h"]];
  const xlab = ticksX.filter(([s]) => s >= Math.min(...secs) && s <= Math.max(...secs)).map(([s, t]) =>
    `<line x1="${X(s).toFixed(1)}" y1="${mT}" x2="${X(s).toFixed(1)}" y2="${mT + ih}" stroke="var(--grid-soft)"/>`
    + `<text x="${X(s).toFixed(1)}" y="${H - 8}" fill="var(--muted)" font-size="9" text-anchor="middle">${t}</text>`).join("");
  const line = (key, color, dash) => {
    const pts = points.filter((p) => p[key] != null).map((p) => `${X(p.seconds).toFixed(1)},${Y(p[key]).toFixed(1)}`);
    if (pts.length < 2) return "";
    return `<path d="M${pts.join(" L")}" fill="none" stroke="${color}" stroke-width="2.2" stroke-linejoin="round" ${dash ? 'stroke-dasharray="5 3"' : ""}/>`;
  };
  const dots = points.filter((p) => p.actual != null).map((p) =>
    `<circle cx="${X(p.seconds).toFixed(1)}" cy="${Y(p.actual).toFixed(1)}" r="2.6" fill="#12A9E0"/>`).join("");

  el.innerHTML =
    `<div class="chart-wrap">
      <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block">
        ${grid}${xlab}${line("predicted", "#1F6BEC", true)}${line("actual", "#12A9E0", false)}${dots}
        <g class="cross"></g>
        <rect x="${mL}" y="${mT}" width="${iw}" height="${ih}" fill="transparent" style="touch-action:none"/>
      </svg><div class="chart-tip" style="display:none"></div>
    </div>`;

  const svg = el.querySelector("svg"), cross = el.querySelector(".cross"), tip = el.querySelector(".chart-tip");
  const move = (clientX) => {
    const r = svg.getBoundingClientRect();
    const vbx = (clientX - r.left) * (W / r.width);
    let best = 0, bd = Infinity;
    points.forEach((p, i) => { const d = Math.abs(X(p.seconds) - vbx); if (d < bd) { bd = d; best = i; } });
    const p = points[best], cx = X(p.seconds).toFixed(1);
    cross.innerHTML = `<line x1="${cx}" y1="${mT}" x2="${cx}" y2="${mT + ih}" stroke="var(--grid-strong)"/>`
      + (p.actual != null ? `<circle cx="${cx}" cy="${Y(p.actual).toFixed(1)}" r="3.5" fill="#12A9E0" stroke="var(--card)" stroke-width="1.5"/>` : "")
      + (p.predicted != null ? `<circle cx="${cx}" cy="${Y(p.predicted).toFixed(1)}" r="3.5" fill="#1F6BEC" stroke="var(--card)" stroke-width="1.5"/>` : "");
    tip.style.display = "block";
    tip.style.left = Math.max(15, Math.min(85, X(p.seconds) / W * 100)) + "%";
    tip.innerHTML = `<b>${fmtDur(p.seconds)}</b>`
      + (p.actual != null ? `<span><i style="background:#12A9E0"></i>real ${Math.round(p.actual)} W</span>` : "")
      + (p.predicted != null ? `<span><i style="background:#1F6BEC"></i>modelo ${Math.round(p.predicted)} W</span>` : "");
  };
  const hide = () => { cross.innerHTML = ""; tip.style.display = "none"; };
  svg.addEventListener("pointermove", (e) => move(e.clientX));
  svg.addEventListener("pointerdown", (e) => move(e.clientX));
  svg.addEventListener("pointerleave", hide);
}

// Gráfica de líneas INTERACTIVA (tocar/pasar el dedo → valores). Soporta un
// tramo futuro punteado (splitIndex = "HOY"). series:[{vals,color,name,fill}].
function mountChart(el, { dates, series, zeroLine = false, yunit = "", splitIndex = null }) {
  const W = 344, H = 188, mL = 38, mR = 12, mT = 12, mB = 22;
  const iw = W - mL - mR, ih = H - mT - mB;
  const all = series.flatMap((s) => s.vals).filter((v) => v != null);
  if (all.length < 2) { el.innerHTML = `<div class="sub">sin datos suficientes</div>`; return; }
  let min = Math.min(...all), max = Math.max(...all);
  if (min === max) { min -= 1; max += 1; }
  const pad = (max - min) * 0.12; min -= pad; max += pad;
  const n = dates.length;
  const X = (i) => mL + (n <= 1 ? iw / 2 : i / (n - 1) * iw);
  const Y = (v) => mT + (1 - (v - min) / (max - min)) * ih;

  let grid = niceTicks(min, max, 4).map((v) => {
    const y = Y(v).toFixed(1);
    return `<line x1="${mL}" y1="${y}" x2="${W - mR}" y2="${y}" stroke="var(--grid)"/>`
      + `<text x="${mL - 5}" y="${(+y + 3).toFixed(1)}" fill="var(--muted)" font-size="9" text-anchor="end">${Math.round(v)}${yunit}</text>`;
  }).join("");
  if (zeroLine && min < 0 && max > 0) {
    const zy = Y(0).toFixed(1);
    grid += `<line x1="${mL}" y1="${zy}" x2="${W - mR}" y2="${zy}" stroke="var(--grid-strong)" stroke-dasharray="3 3"/>`;
  }
  // Línea "HOY" (frontera pasado/futuro)
  if (splitIndex != null && splitIndex < n - 1) {
    const sx = X(splitIndex).toFixed(1);
    grid += `<line x1="${sx}" y1="${mT}" x2="${sx}" y2="${mT + ih}" stroke="var(--grid-strong)" stroke-dasharray="2 3"/>`
      + `<text x="${sx}" y="${mT - 3}" fill="var(--muted)" font-size="9" text-anchor="middle">HOY</text>`;
  }

  const seg = (vals, a, b, color, dash) => {
    const pts = [];
    for (let i = a; i <= b; i++) if (vals[i] != null) pts.push(`${X(i).toFixed(1)},${Y(vals[i]).toFixed(1)}`);
    if (pts.length < 2) return "";
    return `<path d="M${pts.join(" L")}" fill="none" stroke="${color}" stroke-width="2.2" stroke-linejoin="round" ${dash ? 'stroke-dasharray="5 3"' : ""}/>`;
  };
  const paths = series.map((s) => {
    const sp = splitIndex == null ? n - 1 : splitIndex;
    const area = s.fill ? (() => {
      const pts = [];
      for (let i = 0; i < n; i++) if (s.vals[i] != null) pts.push(`${X(i).toFixed(1)},${Y(s.vals[i]).toFixed(1)}`);
      if (pts.length < 2) return "";
      return `<path d="M${pts.join(" L")} L${X(n - 1).toFixed(1)},${(mT + ih).toFixed(1)} L${X(0).toFixed(1)},${(mT + ih).toFixed(1)} Z" fill="${s.color}" opacity="0.08"/>`;
    })() : "";
    return area + seg(s.vals, 0, sp, s.color, false) + seg(s.vals, sp, n - 1, s.color, true);
  }).join("");

  const ticks = [0, Math.floor(n / 2), n - 1].map((i) =>
    `<text x="${X(i).toFixed(1)}" y="${H - 6}" fill="var(--muted)" font-size="9" text-anchor="middle">${dates[i]}</text>`).join("");

  el.innerHTML =
    `<div class="chart-wrap">
      <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block">
        ${grid}${paths}${ticks}
        <g class="cross"></g>
        <rect x="${mL}" y="${mT}" width="${iw}" height="${ih}" fill="transparent" style="touch-action:none"/>
      </svg>
      <div class="chart-tip" style="display:none"></div>
    </div>`
    + legend(series.filter((s) => s.name).map((s) => ({ color: s.color, label: s.name })));

  const svg = el.querySelector("svg");
  const cross = el.querySelector(".cross");
  const tip = el.querySelector(".chart-tip");
  const move = (clientX) => {
    const r = svg.getBoundingClientRect();
    const vbx = (clientX - r.left) * (W / r.width);
    let i = Math.round((vbx - mL) / iw * (n - 1));
    i = Math.max(0, Math.min(n - 1, i));
    const cx = X(i).toFixed(1);
    cross.innerHTML = `<line x1="${cx}" y1="${mT}" x2="${cx}" y2="${mT + ih}" stroke="var(--grid-strong)"/>`
      + series.map((s) => s.vals[i] == null ? "" :
        `<circle cx="${cx}" cy="${Y(s.vals[i]).toFixed(1)}" r="3" fill="${s.color}" stroke="var(--card)" stroke-width="1.5"/>`).join("");
    tip.style.display = "block";
    tip.style.left = Math.max(15, Math.min(85, X(i) / W * 100)) + "%";
    tip.innerHTML = `<b>${dates[i] || ""}</b>` + series.map((s) => s.vals[i] == null ? "" :
      `<span><i style="background:${s.color}"></i>${s.name || ""} ${s.vals[i]}${yunit}</span>`).join("");
  };
  const hide = () => { cross.innerHTML = ""; tip.style.display = "none"; };
  svg.addEventListener("pointermove", (e) => move(e.clientX));
  svg.addEventListener("pointerdown", (e) => move(e.clientX));
  svg.addEventListener("pointerleave", hide);
}

// Zona de potencia (color) según el %FTP superior del bloque.
function zoneFor(target) {
  const m = target.match(/(\d+)\s*[–\-]\s*(\d+)\s*%/);
  const hi = m ? +m[2] : 70;
  if (hi < 56) return "z1";
  if (hi < 76) return "z2";
  if (hi < 88) return "z3";
  if (hi < 106) return "z4";
  if (hi < 121) return "z5";
  return "z6";
}
function blockHtml(t) {
  const m = t.match(/(\d[\d–\-]*\s*W)/);
  const w = m ? m[1] : "", label = w ? t.replace(w, "").trim() : t;
  return `<div class="vk-block"><i class="vk-rail ${zoneFor(t)}"></i><span>${label}</span><b>${w}</b></div>`;
}

// Tile de forma: el TSB sobre el medidor, con los cortes REALES del atleta en
// el eje. Los extremos no son -33/+23 fijos: salen de sus percentiles.
function formTile(s) {
  if (s.tsb == null) return "";
  const th = s.thresholds || {};
  const lo = (th.recovery != null ? th.recovery : -25) - 8;
  const hi = (th.fresh != null ? th.fresh : 15) + 8;
  const pos = Math.max(2, Math.min(98, (s.tsb - lo) / (hi - lo) * 100));
  return `<div class="vk-tiles"><div class="vk-tile t-form vk-tile--wide">
    <div class="vk-section" style="margin:0"><span class="vk-key">Forma</span>
      <small>${s.form_label || ""}</small></div>
    <b>${signed(s.tsb)}</b>
    <div class="vk-gauge"><b style="left:${pos}%"></b></div>
    <div class="vk-axis"><span>${Math.round(lo)}</span><span>0</span><span>+${Math.round(hi)}</span></div>
  </div></div>`;
}

// "Por qué" del plan: el motor devuelve una frase con anotaciones entre
// corchetes ([simulado: …], [ajuste por seguridad: …]). En vez de volcarla cruda
// (quedaba fea), la partimos: frase principal + cada anotación como chip.
function whyCard(rationale) {
  if (!rationale) return "";
  const notes = [...rationale.matchAll(/\[([^\]]+)\]/g)].map((m) => m[1].trim());
  let main = rationale.replace(/\[[^\]]*\]/g, " ").replace(/\s+/g, " ").trim();
  main = main.replace(/^Objetivo:\s*[\w_ ]+—\s*/i, "");        // ya se ve arriba
  const chips = notes.map((n) => {
    const [head, ...rest] = n.split(":");
    const body = rest.join(":").trim();
    return `<div class="vk-note"><b>${head.trim()}</b><span>${body || ""}</span></div>`;
  }).join("");
  return `<div class="vk-panel"><div class="vk-key">Por qué</div>
    <p class="vk-prose">${main}</p>${chips}</div>`;
}

// Esqueletos: mientras llegan los datos se ve la FORMA de la pantalla.
const SKELETON = {
  home: `<div class="vk-sk" style="height:210px"></div>
    <div class="vk-sk" style="height:104px;margin-top:12px"></div>
    <div class="vk-sk" style="height:86px;margin-top:12px"></div>`,
  rows: `${'<div class="vk-sk" style="height:52px;margin-bottom:10px"></div>'.repeat(6)}`,
  cards: `<div class="vk-sk" style="height:150px;margin-bottom:12px"></div>
    <div class="vk-sk" style="height:150px"></div>`,
};

// --- Pantalla HOY -----------------------------------------------------------
function renderHome(s) {
  const p = s.plan;
  const planLabel = s.trained_today ? "Mañana" : "Sesión de hoy";
  let planHtml = `<div class="vk-empty">No hay plan todavía: falta estimar tu FTP.</div>`;
  if (p) {
    const chips = [];
    if (s.trained_today) {
      chips.push(`<span class="vk-chip vk-chip--ok">ya entrenaste hoy · ${hhmm(s.trained_minutes)} h</span>`);
    }
    if (p.aspired) {
      chips.push(`<span class="vk-chip vk-chip--adjust">rebajado desde ${p.aspired}</span>`);
    }
    const meta = [
      `${hhmm(p.minutes)} h`,
      p.tss != null ? `${p.tss} TSS` : null,
      p.intensity != null ? `IF ${p.intensity.toFixed(2)}` : null,
    ].filter(Boolean).join(" · ");
    planHtml = `<div class="vk-panel vk-panel--ink">
      <div class="vk-key">${planLabel}</div>
      <h1 class="vk-hero-title">${p.session}</h1>
      <div class="vk-hero-meta">${meta}</div>
      ${chips.length ? `<div class="vk-chips" style="margin-top:14px">${chips.join("")}</div>` : ""}
      <div class="vk-blocks">${(p.targets || []).map(blockHtml).join("")}</div>
    </div>`;
  }

  // Retícula: solo cifras con unidad y ventana temporal (regla 5 del sistema).
  const stats = [
    s.days_to_event != null ? [s.days_to_event, "días meta"] : null,
    [fmt(s.atl), "atl 7d"],
    p && p.intensity != null ? [p.intensity.toFixed(2), "if hoy"] : null,
    [fmt(s.ctl), "ctl 42d"],
  ].filter(Boolean);

  const engine = [
    s.ftp != null ? `<div class="vk-row"><span class="vk-key">FTP</span><b>${fmt(s.ftp)} W</b></div>` : "",
    s.cp != null ? `<div class="vk-row"><span class="vk-key">CP</span><b>${fmt(s.cp)} W</b></div>` : "",
    s.w_prime != null ? `<div class="vk-row"><span class="vk-key">W′</span><b>${(s.w_prime / 1000).toFixed(1)} kJ</b></div>` : "",
    s.cri_coverage != null
      ? `<div class="vk-row"><span class="vk-key">CRI</span><b>${fmt(s.cri)}</b><span class="vk-trail">cobertura ${Math.round(s.cri_coverage * 100)} %</span></div>` : "",
  ].join("");

  const goal = s.goal_date ? `<div class="vk-panel">
    <div class="vk-section" style="margin:0"><span class="vk-key">Objetivo</span>
      <span class="vk-key">fase ${s.phase}</span></div>
    <div class="vk-row" style="border:none"><span>${s.goal_name || "Evento"}</span>
      <b style="margin-left:auto">${shortDate(s.goal_date)} · ${s.days_to_event} d</b></div>
  </div>` : "";

  $("#home-content").innerHTML = `${planHtml}
    ${formTile(s)}
    <div class="vk-tiles vk-tiles--3">
      <div class="vk-tile t-fitness vk-tile--compact"><span class="vk-key">Fitness</span><b>${fmt(s.ctl)}</b><small>CTL 42 d</small></div>
      <div class="vk-tile t-cri vk-tile--compact"><span class="vk-key">CRI</span><b>${fmt(s.cri)}</b><small>disposición</small></div>
      <div class="vk-tile t-engine vk-tile--compact"><span class="vk-key">FTP</span><b>${fmt(s.ftp)}</b><small>watts</small></div>
    </div>
    <div class="vk-stats">${stats.map(([v, k]) =>
      `<div class="vk-stat"><b>${v}</b><span>${k}</span></div>`).join("")}</div>
    ${goal}
    <div class="vk-panel">
      <div class="vk-section" style="margin:0"><span class="vk-key">Forma y predicción</span>
        <span class="vk-key">60 d + 7</span></div>
      <div id="form-svg" style="margin-top:10px"><div class="vk-sk" style="height:150px"></div></div>
    </div>
    ${engine ? `<div class="vk-panel"><div class="vk-key">Motor</div>
      <div class="vk-rows" style="margin-top:8px">${engine}</div></div>` : ""}
    ${p ? whyCard(p.rationale) : ""}`;
  // gráfica de forma + predicción (interactiva)
  api("/api/form-forecast?past=60&future=7").then((t) => {
    if (!t.length) { $("#form-svg").innerHTML = `<div class="vk-empty">Sin datos todavía.</div>`; return; }
    let split = t.findIndex((d) => d.projected);
    split = split < 0 ? t.length - 1 : split - 1;
    mountChart($("#form-svg"), {
      dates: labelDates(t.map((d) => d.day)),
      series: [
        { vals: t.map((d) => d.ctl), color: "var(--zone-2)", name: "Fitness (CTL)", fill: true },
        { vals: t.map((d) => d.tsb), color: "var(--accent)", name: "Forma (TSB)" },
      ],
      zeroLine: true, splitIndex: split,
    });
  }).catch(() => { $("#form-svg").innerHTML = `<div class="vk-empty">—</div>`; });
}

// --- Pantalla ACTIVIDADES ---------------------------------------------------
const ZONE_OF_IF = (i) => (i == null ? "z2" : i < 0.60 ? "z1" : i < 0.76 ? "z2"
  : i < 0.88 ? "z3" : i < 1.00 ? "z4" : "z5");

function renderActivities(list) {
  const box = $("#activities-content");
  if (!list.length) {
    box.innerHTML = `<div class="vk-empty">Aún no hay entrenamientos importados.</div>`;
    return;
  }
  box.innerHTML = `<div class="vk-section"><span class="vk-key">Tus entrenamientos</span>
      <span class="vk-key">desde Strava</span></div>` + list.map((a, i) => {
    const stat = (v, k) => v == null ? "" : `<div class="am"><b>${v}</b><span>${k}</span></div>`;
    return `<div class="vk-panel acard" data-i="${i}">
      <div class="arow">
        <i class="vk-rail ${ZONE_OF_IF(a.intensity)}" style="height:34px"></i>
        <span class="ao"><b>${a.name || "Entrenamiento"}</b>
          <span>${new Date(a.day).toLocaleDateString("es-ES", { weekday: "short", day: "numeric", month: "short" })}${a.session_label ? " · " + a.session_label : ""}</span></span>
      </div>
      <div class="amets">
        ${stat(hhmm(a.minutes), "tiempo")}
        ${stat(a.distance_km, "km")}
        ${stat(a.np_w, "W norm")}
        ${stat(a.tss, "TSS")}
      </div>
      <div class="adetail" hidden data-id="${a.id}">
        <div class="vk-sk" style="height:120px;margin-top:12px"></div>
      </div>
    </div>`;
  }).join("");
  box.querySelectorAll(".acard").forEach((c) => {
    c.querySelector(".arow").addEventListener("click", async () => {
      const d = c.querySelector(".adetail");
      const open = !d.hidden;
      d.hidden = open;
      if (!open && !d.dataset.loaded) {        // detalle bajo demanda
        d.dataset.loaded = "1";
        try { d.innerHTML = activityDetailHtml(await api(`/api/activity/${d.dataset.id}`)); }
        catch (e) { d.innerHTML = `<div class="vk-empty">${e.detail || "Error."}</div>`; }
      }
    });
  });
}

// Ficha del entrenamiento: qué fue realmente + métricas + zonas + series.
function activityDetailHtml(d) {
  const stat = (v, k) => v == null ? "" : `<div class="am"><b>${v}</b><span>${k}</span></div>`;
  const totalZ = (d.zones || []).reduce((s, z) => s + z.seconds, 0) || 1;
  const zbar = (d.zones || []).map((z) => {
    const cls = "z" + (z.zone.match(/Z(\d)/) || [0, 2])[1];
    return `<div class="vk-zrow"><span style="width:88px">${z.zone}</span>
      <span class="vk-ztrack"><span class="${cls}" style="width:${(z.seconds / totalZ * 100).toFixed(1)}%"></span></span>
      <span class="vk-zv">${hhmm(z.seconds / 60)}</span></div>`;
  }).join("");
  const reps = (d.intervals || []).map((v, i) =>
    `<div class="irow"><span>#${i + 1}</span><b>${hhmm(v.seconds / 60)}</b>
      <span>${v.avg_w} W</span><span class="ipct">${v.pct_ftp} % FTP</span></div>`).join("");
  return `
    ${d.session_label ? `<div class="vk-chips" style="margin-top:12px">
      <span class="vk-chip">${d.session_label}${d.detected ? ` · ${d.detected}` : ""}</span></div>` : ""}
    <p class="vk-prose">${d.text}</p>
    <div class="amets">
      ${stat(d.avg_power_w, "W media")}${stat(d.np_w, "W norm")}
      ${stat(d.max_power_w, "W máx")}${stat(d.avg_hr, "ppm medio")}
      ${stat(d.max_hr, "ppm máx")}${stat(d.avg_cadence, "rpm")}
      ${stat(d.elevation_m, "m desnivel")}${stat(d.intensity, "IF")}
      ${stat(d.kilojoules, "kJ")}${stat(d.tss, "TSS")}
    </div>
    ${zbar ? `<div class="vk-key" style="margin-top:14px">Tiempo en zonas</div>
      <div style="margin-top:8px">${zbar}</div>` : ""}
    ${reps ? `<div class="vk-key" style="margin-top:14px">Series detectadas</div>
      <div style="margin-top:4px">${reps}</div>` : ""}`;
}

let activitiesLoaded = false;
async function loadActivities() {
  if (activitiesLoaded) return;
  $("#activities-content").innerHTML = SKELETON.rows;
  try { renderActivities(await api("/api/activities?limit=30")); activitiesLoaded = true; }
  catch (e) { $("#activities-content").innerHTML = `<div class="vk-empty">${e.detail || "Error."}</div>`; }
}

// --- Pantalla PROGRESO ------------------------------------------------------
async function renderProgress() {
  const box = $("#progress-content");
  box.innerHTML = SKELETON.cards;
  const [ftp, pc, comp, chk] = await Promise.all([
    api("/api/ftp").catch(() => []),
    api("/api/power-curve").catch(() => null),
    api("/api/compliance?days=28").catch(() => null),
    api("/api/checkin").catch(() => null),
  ]);
  let html = "";
  // Curva de potencia (real vs modelo, interactiva) + coherencia del CP
  if (pc) {
    html += `<div class="vk-panel">
      <div class="vk-section" style="margin:0"><span class="vk-key">Curva de potencia</span>
        <span class="vk-key">120 días</span></div>
      <div id="pc-chart" style="margin-top:10px"></div>
      <div class="vk-chips" style="margin-top:10px">
        <span class="vk-chip"><i style="width:8px;height:8px;border-radius:999px;background:var(--accent);display:inline-block"></i>Real</span>
        <span class="vk-chip"><i style="width:8px;height:8px;border-radius:999px;background:var(--zone-2);display:inline-block"></i>Modelo CP/W′</span>
        ${pc.verdict ? `<span class="vk-chip ${pc.coherent ? "vk-chip--ok" : "vk-chip--warn"}">${pc.verdict}</span>` : ""}
      </div></div>`;
  }
  // Tu motor: CP y W′ como tiles tintados.
  if (ftp.length) {
    const cur = ftp[ftp.length - 1];
    html += `<div class="vk-tiles">
      <div class="vk-tile t-engine"><span class="vk-key">FTP</span><b>${cur.ftp}</b><small>watts</small></div>
      <div class="vk-tile t-load"><span class="vk-key">CP</span><b>${cur.cp}</b><small>watts</small></div>
    </div>`;
  }
  // Cumplimiento del plan: lo prescrito vs lo hecho (cierra el bucle).
  if (comp) {
    if (comp.rate == null) {
      html += `<div class="vk-panel"><div class="vk-key">Cumplimiento</div>
        <p class="vk-prose">${comp.note}</p></div>`;
    } else {
      const ST = {
        cumplido: ["vk-chip--ok", "Cumplido"], descanso_ok: ["vk-chip--ok", "Descanso"],
        "más": ["vk-chip--warn", "Más"], menos: ["vk-chip--warn", "Menos"],
        distinto: ["vk-chip--warn", "Otra"], no_hecho: ["vk-chip--bad", "No hecho"],
        extra: ["vk-chip--accent", "Extra"],
      };
      const rows = comp.days.slice(-10).reverse().map((d) => {
        const [cls, label] = ST[d.status] || ["", d.status];
        return `<div class="cmrow"><span class="cmd">${shortDate(d.day)}</span>
          <span class="vk-chip ${cls}">${label}</span>
          <span class="cmn">${d.note}</span></div>`;
      }).join("");
      const pct = Math.round(comp.rate * 100);
      html += `<div class="vk-panel">
        <div class="vk-section" style="margin:0"><span class="vk-key">Cumplimiento · 28 d</span>
          <span style="font-family:var(--font-display);font-weight:700;font-size:var(--text-21)">${pct} %</span></div>
        <div class="vk-bar" style="margin-top:10px"><span style="width:${pct}%"></span></div>
        <div class="vk-axis"><span>${comp.tss_done} TSS hecho</span><span>${comp.tss_planned} previsto</span></div>
        <div style="margin-top:8px">${rows}</div></div>`;
    }
  }
  // Check-in diario: sueño + sensación. Es la única entrada de recuperación
  // que tenemos sin wearable, y alimenta el CRI.
  if (chk) html += checkinCard(chk);
  box.innerHTML = html || `<div class="vk-empty">Sin datos de potencia todavía.</div>`;
  if (pc) mountPowerChart($("#pc-chart"), pc.points, pc.cp);
  if (chk) wireCheckin();
}

// --- Check-in diario (sueño + sensación) ------------------------------------
const FEEL_WORDS = {
  1: "reventado", 2: "muy cansado", 3: "cansado", 4: "flojo", 5: "regular",
  6: "bien", 7: "bastante bien", 8: "fuerte", 9: "muy fuerte", 10: "eufórico",
};

function checkinCard(c) {
  const done = !c.pending;
  const sleep = c.sleep_hours ?? c.last_sleep ?? 7.5;
  const feel = Math.round(c.feel ?? c.last_feel ?? 6);
  return `<div class="vk-panel" id="checkin">
    <div class="vk-key">Check-in de hoy</div>
    <p class="vk-prose">${done
      ? "Registrado hoy. Puedes corregirlo si quieres."
      : "Sin reloj no se puede medir el sueño, así que se pregunta. Tu percepción alimenta la recuperación del CRI."}</p>
    <div style="display:flex;flex-direction:column;gap:12px;margin-top:14px">
      <div class="ctl"><span class="vk-key">Sueño</span>
        <input class="slider" type="range" id="ck-sleep" min="0" max="12" step="0.25" value="${sleep}" />
        <b id="ck-sleepl">${hhmm(sleep * 60)} h</b></div>
      <div class="ctl"><span class="vk-key">Sensación</span>
        <input class="slider" type="range" id="ck-feel" min="1" max="10" step="1" value="${feel}" />
        <b id="ck-feell">${feel}/10</b></div>
      <button class="vk-btn" id="ck-save">${done ? "Actualizar" : "Guardar check-in"}</button>
      <span class="vk-key" id="ck-msg"></span>
    </div>
  </div>`;
}

function wireCheckin() {
  const s = $("#ck-sleep"), f = $("#ck-feel"), msg = $("#ck-msg");
  if (!s) return;
  s.addEventListener("input", () => { $("#ck-sleepl").textContent = hhmm(+s.value * 60) + " h"; });
  f.addEventListener("input", () => {
    $("#ck-feell").textContent = `${f.value}/10`;
    f.title = FEEL_WORDS[f.value];
  });
  $("#ck-save").addEventListener("click", async () => {
    msg.textContent = "Guardando…"; msg.className = "vk-key";
    try {
      await api("/api/checkin", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sleep_hours: +s.value, feel: +f.value }),
      });
      msg.textContent = "Guardado ✓"; msg.className = "vk-key" ; msg.style.color = "var(--ok)";
      checkinPending = false;
      loadHome();                    // el CRI cambia: refresca la portada
    } catch (e) {
      msg.textContent = e.detail || "No se pudo guardar."; msg.className = "vk-key" ; msg.style.color = "var(--bad)";
    }
  });
}

const OBJECTIVES = [
  ["auto", "Que decida Vikon"], ["rest", "Descanso"], ["recovery", "Recuperación"],
  ["endurance", "Resistencia (Z2)"], ["sweet_spot", "Sweet Spot"],
  ["threshold", "Umbral"], ["vo2max", "VO2máx"],
];
const PHASE_ES = {
  off: "sin meta", base: "base", build: "construcción",
  peak: "punta", taper: "descarga", race: "carrera",
};

function renderHorizon(days) {
  if (!days.length) { $("#horizon-content").innerHTML = `<div class="vk-empty">Sin datos.</div>`; return; }
  const names = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];
  const totalTss = days.reduce((s, h) => s + (h.tss || 0), 0);
  $("#horizon-content").innerHTML = `
    <div class="vk-section"><span class="vk-key">Próximos 7 días</span>
      <span class="vk-key">solo hoy se compromete</span></div>` +
    days.map((h, i) => {
    const d = i === 0 ? "Hoy" : names[new Date(h.day).getDay()];
    const blocks = (h.targets && h.targets.length)
      ? `<div class="vk-blocks">${h.targets.map(blockHtml).join("")}</div>` : "";
    const why = (h.rationale || "").replace(/\[/g, "· ").replace(/\]/g, "");
    return `<div class="hitem" data-day="${h.day}">
      <button class="hrow"><span class="hd">${d}</span>
        <span class="ho"><b>${h.session}</b>
          <span>${h.objective.replace("_", " ")}${h.intensity != null ? ` · IF ${h.intensity.toFixed(2)}` : ""}</span></span>
        <span class="ht"><b>${h.minutes ? hhmm(h.minutes) + " h" : "libre"}</b><span>${h.tss} TSS</span></span></button>
      <div class="hdetail" hidden>
        ${h.description ? `<p class="vk-prose" style="margin:0 0 12px">${h.description}</p>` : ""}
        <div class="hstats">
          <div class="hs"><b>${hhmm(h.minutes)}</b><span>duración</span></div>
          <div class="hs"><b>${h.tss}</b><span>TSS</span></div>
          <div class="hs"><b>${h.intensity != null ? h.intensity.toFixed(2) : "—"}</b><span>IF</span></div>
          <div class="hs"><b>${signed(h.tsb)}</b><span>forma</span></div>
          <div class="hs"><b>${h.ctl}</b><span>fitness</span></div>
          <div class="hs"><b>${PHASE_ES[h.phase] || h.phase}</b><span>fase</span></div>
        </div>
        ${blocks}
        ${why ? `<div class="vk-note" style="margin-top:12px"><b>Por qué</b><span>${why}</span></div>` : ""}
        <div style="margin-top:14px;display:flex;flex-direction:column;gap:10px">
          <div class="ctl"><span class="vk-key">Tiempo</span>
            <input class="slider dmin" type="range" min="0" max="360" step="15" value="${h.minutes || 0}" />
            <b class="dminl">${h.minutes ? hhmm(h.minutes) : "libre"}</b></div>
          <div class="ctl"><span class="vk-key">Sesión</span>
            <select class="vk-input dobj">${OBJECTIVES.map(([v, l]) =>
              `<option value="${v}">${l}</option>`).join("")}</select></div>
          <button class="vk-btn dsave" style="padding:11px;font-size:14px">Guardar este día</button>
          <span class="vk-key dmsg"></span>
        </div>
      </div>
    </div>`;
  }).join("") + `
    <div class="vk-panel" style="margin-top:4px">
      <div class="vk-key">Carga de la semana</div>
      <div class="vk-axis" style="margin-top:10px"><span>${totalTss} TSS previstos en 7 días</span>
        <span>${Math.round(totalTss / 7)} TSS/día</span></div>
    </div>`;

  $("#horizon-content").querySelectorAll(".hitem").forEach((item) => {
    const row = item.querySelector(".hrow"), det = item.querySelector(".hdetail");
    row.addEventListener("click", async () => {
      const open = !det.hidden;
      det.hidden = open;
      row.classList.toggle("is-open", !open);
      if (!open && !det.dataset.init) {          // estado guardado de ese día
        det.dataset.init = "1";
        try {
          const cfg = await api(`/api/day/${item.dataset.day}`);
          if (cfg.objective) det.querySelector(".dobj").value = cfg.objective;
        } catch (_) {}
      }
    });
    const slider = item.querySelector(".dmin"), lbl = item.querySelector(".dminl");
    slider.addEventListener("input", () => {
      lbl.textContent = Number(slider.value) ? hhmm(slider.value) : "libre";
    });
    item.querySelector(".dsave").addEventListener("click", async (ev) => {
      const btn = ev.currentTarget, msg = item.querySelector(".dmsg");
      btn.disabled = true; msg.textContent = "Guardando…";
      try {
        await api("/api/day", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            date: item.dataset.day,
            minutes: Number(slider.value),
            objective: item.querySelector(".dobj").value,
          }),
        });
        msg.textContent = "✓ guardado";
        horizonLoaded = false; loadHorizon(); loadHome();
      } catch (e) { msg.textContent = e.detail || "Error"; btn.disabled = false; }
    });
  });
}

// --- Pantalla AJUSTES -------------------------------------------------------
async function renderSettings() {
  const box = $("#settings-content");
  box.innerHTML = SKELETON.cards;
  let s;
  try { s = await api("/api/settings"); }
  catch (e) { box.innerHTML = `<div class="vk-empty">${e.detail || "Error cargando ajustes."}</div>`; return; }

  const wp = s.w_prime != null ? (s.w_prime / 1000).toFixed(1) + " kJ" : "—";
  const goal = s.goal
    ? `<div class="vrow"><span>${s.goal.name || "Evento"}</span><span class="vval">${shortDate(s.goal.date)} · faltan ${s.goal.days_to} d</span></div>`
    : `<div class="sub">Sin objetivo. Añade uno para activar la periodización (fase/taper).</div>`;
  const llmHost = (() => { try { return new URL(s.llm.base_url).host; } catch { return s.llm.base_url; } })();
  const st = s.strava || { connected: false, can_connect: false };

  box.innerHTML = `
    <div class="vk-panel"><div class="vk-panel-title">Perfil y disponibilidad</div>
      <div class="sub">Tus datos y cuánto tiempo tienes cada día. El plan encaja las sesiones en tu disponibilidad.</div>
      <button id="edit-profile" class="vk-btn vk-btn--quiet" style="margin-top:10px">Editar perfil y disponibilidad</button>
    </div>

    <div class="vk-panel"><div class="vk-panel-title">Tu motor</div>
      <div class="vrow"><span>FTP</span><span class="vval">${fmt(s.ftp)} W</span></div>
      <div class="vrow"><span>CP (potencia crítica)</span><span class="vval">${fmt(s.cp)} W</span></div>
      <div class="vrow"><span>W′ (reserva anaeróbica)</span><span class="vval">${wp}</span></div>
      <div class="sub" style="margin-top:8px">Se recalibra solo cuando entran entrenamientos nuevos con potencia.</div>
      <button id="calib-now" class="vk-btn vk-btn--quiet">Recalibrar ahora</button>
      <div id="calib-msg" class="sub"></div>
    </div>

    <div class="vk-panel"><div class="vk-panel-title">Objetivo</div>
      ${goal}
      <div class="goalform">
        <input class="vk-input" id="goal-name" placeholder="Nombre (p. ej. Gran Fondo de León)" autocomplete="off" />
        <input class="vk-input" id="goal-date" type="date" />
        <select class="vk-input" id="goal-kind">${kindOptions()}</select>
        <select class="vk-input" id="goal-prio"><option value="A">A · principal</option><option value="B">B</option><option value="C">C</option></select>
        <button class="vk-btn" id="goal-save">Guardar objetivo</button>
      </div>
      <div id="goal-msg" class="sub"></div>
    </div>

    <div class="vk-panel"><div class="vk-panel-title">Datos</div>
      <div class="vrow"><span>Strava</span><span class="vval">${st.connected
        ? '<span class="dot" style="background:var(--ok)"></span>conectado' : '<span class="dot" style="background:var(--warn)"></span>sin conectar'}</span></div>
      <div class="vrow"><span>Actividades importadas</span><span class="vval">${s.activities}</span></div>
      <div class="vrow"><span>Última actividad</span><span class="vval">${s.last_activity ? shortDate(s.last_activity) : "—"}</span></div>
      ${st.connected
        ? `<div class="sub" style="margin:8px 0">Los entrenamientos entran solos desde Strava. Puedes forzar una sincronización ahora:</div>
           <button id="sync-now" class="vk-btn vk-btn--quiet">${icon("refresh", 18)} Sincronizar con Strava</button>
           <div id="sync-msg" class="sub"></div>
           <button id="strava-off" class="vk-btn vk-btn--quiet" style="margin-top:8px;background:var(--card-2);color:var(--muted)">Desconectar Strava</button>`
        : st.can_connect
          ? `<div class="sub" style="margin:8px 0">Este perfil aún no tiene Strava. Conéctalo para que tus entrenamientos entren solos.</div>
             <button id="strava-on" class="vk-btn vk-btn--quiet">Conectar mi Strava</button>`
          : `<div class="sub" style="margin:8px 0">Faltan las credenciales de Strava en el archivo <code>.env</code> del servidor.</div>`}
      <div id="strava-msg" class="sub"></div>
    </div>

    <div class="vk-panel"><div class="vk-panel-title">Vikon IA</div>
      <div class="vrow"><span>Estado</span><span class="vval">${s.llm.configured
        ? '<span class="dot" style="background:var(--ok)"></span>conectada' : '<span class="dot" style="background:var(--warn)"></span>sin clave'}</span></div>
      <div class="vrow"><span>Modelo</span><span class="vval">${s.llm.model}</span></div>
      <div class="vrow"><span>Proveedor</span><span class="vval">${llmHost}</span></div>
      ${s.llm.configured ? "" : `<div class="sub" style="margin-top:8px">Añade tu clave en el archivo <code>.env</code> para activar el chat.</div>`}
    </div>

    <div class="vk-panel"><div class="vk-panel-title">Legal</div>
      <div class="sub">Vikon planifica entrenamiento deportivo. <strong>No es consejo médico</strong>: consulta a tu médico antes de empezar, y para si notas dolor en el pecho, mareo o falta de aire.</div>
      <div class="vrow"><span>Privacidad</span><span class="vval"><a href="/static/privacidad.html" target="_blank" rel="noopener">Ver</a></span></div>
      <div class="vrow"><span>Términos de servicio</span><span class="vval"><a href="/static/terminos.html" target="_blank" rel="noopener">Ver</a></span></div>
    </div>

    <div class="vk-panel"><div class="vk-panel-title">Tus datos</div>
      <div class="sub">Puedes llevarte todo lo que Vikon guarda sobre ti, o borrarlo por completo.</div>
      <button id="export-data" class="vk-btn vk-btn--quiet" style="margin-top:12px;background:var(--card-2);color:var(--text)">Descargar mis datos</button>
      <button id="delete-account" class="vk-btn vk-btn--quiet" style="margin-top:8px;background:var(--card-2);color:#C0392B">Borrar mi cuenta</button>
      <div id="account-msg" class="sub" style="margin-top:8px"></div>
    </div>

    <div class="vk-panel"><div class="vk-panel-title">Cuenta</div>
      <div class="sub">Vikon · entrenador de ciclismo con gemelo digital. El motor decide; la IA explica.</div>
      <button id="logout-btn" class="vk-btn vk-btn--quiet" style="margin-top:12px;background:var(--card-2);color:var(--text)">Cerrar sesión</button>
    </div>`;

  $("#edit-profile").addEventListener("click", () => showProfile({ onboarding: false }));
  $("#logout-btn").addEventListener("click", logout);
  $("#export-data").addEventListener("click", () => {
    // Descarga directa: el endpoint ya manda Content-Disposition.
    window.location.href = "/api/account/export";
  });
  $("#delete-account").addEventListener("click", deleteAccount);
  const cal = $("#calib-now");
  if (cal) cal.addEventListener("click", async (ev) => {
    const btn = ev.currentTarget, msg = $("#calib-msg");
    btn.disabled = true; msg.textContent = "Recalculando (unos segundos)…";
    try {
      const r = await api("/api/calibrate", { method: "POST" });
      msg.textContent = r.ran
        ? `✓ FTP ${r.ftp} W · CP ${r.cp} W${r.delta_ftp ? ` (${r.delta_ftp > 0 ? "+" : ""}${r.delta_ftp} W)` : ""}`
        : `Sin cambios: ${r.reason}`;
      loadHome(); renderSettings();
    } catch (e) { msg.textContent = e.detail || "Error"; }
    finally { btn.disabled = false; }
  });

  $("#goal-save").addEventListener("click", async () => {
    const date = $("#goal-date").value;
    if (!date) { $("#goal-msg").textContent = "Elige una fecha."; return; }
    try {
      const r = await api("/api/goal", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: $("#goal-name").value || null, date,
          kind: $("#goal-kind").value || null, priority: $("#goal-prio").value,
        }),
      });
      $("#goal-msg").textContent = `Objetivo guardado (faltan ${r.days_to} días).`;
      loadHome(); horizonLoaded = false; activitiesLoaded = false;
      renderSettings();
    } catch (e) { $("#goal-msg").textContent = e.detail || "No se pudo guardar."; }
  });

  const sync = $("#sync-now");
  if (sync) sync.addEventListener("click", async (ev) => {
    const btn = ev.currentTarget; btn.disabled = true;
    $("#sync-msg").textContent = "Sincronizando con Strava…";
    try {
      const r = await api("/api/sync", { method: "POST" });
      $("#sync-msg").textContent = r.new > 0
        ? `✓ ${r.new} nueva(s) actividad(es) importada(s).`
        : "✓ Ya estaba todo al día.";
      if (r.new > 0) { loadHome(); horizonLoaded = false; activitiesLoaded = false; renderSettings(); }
    } catch (e) { $("#sync-msg").textContent = e.detail || "No se pudo sincronizar (¿credenciales de Strava?)."; }
    finally { btn.disabled = false; }
  });

  // Conectar Strava: cada perfil el suyo. Se sale a Strava y se vuelve al
  // callback, que ata la cuenta a ESTE usuario por el `state` firmado.
  const on = $("#strava-on");
  if (on) on.addEventListener("click", async (ev) => {
    ev.currentTarget.disabled = true;
    $("#strava-msg").textContent = "Abriendo Strava…";
    try {
      const r = await api("/api/strava/authorize");
      window.location.href = r.url;
    } catch (e) {
      $("#strava-msg").textContent = e.detail || "No se pudo iniciar la conexión.";
      ev.currentTarget.disabled = false;
    }
  });

  const off = $("#strava-off");
  if (off) off.addEventListener("click", async () => {
    if (!confirm("¿Desconectar Strava de este perfil?\n\nTus entrenamientos ya importados NO se borran.")) return;
    try {
      await api("/api/strava/disconnect", { method: "POST" });
      renderSettings();
    } catch (e) { $("#strava-msg").textContent = e.detail || "No se pudo desconectar."; }
  });
}

// Resultado de volver del OAuth de Strava (?strava=...). Se limpia la URL para
// que no reaparezca el mensaje al recargar.
const STRAVA_BACK = {
  ok: ["ok", "✓ Strava conectado. Tus entrenamientos empezarán a entrar solos."],
  error: ["bad", "No se pudo conectar con Strava. Inténtalo otra vez."],
  expired: ["bad", "La conexión caducó (tardó demasiado). Vuelve a intentarlo."],
  taken: ["bad", "Esa cuenta de Strava ya está enlazada a otro perfil."],
};

function handleStravaReturn() {
  const p = new URLSearchParams(window.location.search).get("strava");
  if (!p) return;
  history.replaceState({}, "", window.location.pathname);
  const [cls, text] = STRAVA_BACK[p] || ["bad", "Respuesta desconocida de Strava."];
  const el = document.createElement("div");
  el.className = "vk-toast vk-toast--" + cls;
  el.textContent = text;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 6000);
  if (p === "ok") { show("settings"); syncThenLoad(); }
}

// --- Acceso (login / registro) ----------------------------------------------
function showAuth(hasUsers) {
  let mode = hasUsers ? "login" : "register";
  const submit = async () => {
    const username = $("#au-user").value.trim(), password = $("#au-pass").value;
    if (!username || !password) { $("#au-msg").textContent = "Rellena usuario y contraseña."; return; }
    const btn = $("#au-submit"); btn.disabled = true;
    const body = { username, password };
    if (mode === "register") {
      const consent = $("#au-consent");
      if (!consent || !consent.checked) {
        $("#au-msg").textContent = "Necesitas aceptar los términos para crear la cuenta.";
        btn.disabled = false; return;
      }
      body.accepted_terms = true;
      body.terms_version = LEGAL_VERSION;
      body.ai_consent = !!($("#au-consent-ai") && $("#au-consent-ai").checked);
    }
    try {
      await api("/api/" + mode, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      $("#auth").classList.remove("is-open");
      // PWA: registra el service worker para poder instalarla en el móvil.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () =>
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {}));
}

boot();                       // ahora autenticado → onboarding o app
    } catch (e) { $("#au-msg").textContent = e.detail || "No se pudo."; btn.disabled = false; }
  };
  const render = () => {
    $("#auth-card").innerHTML = `
      <h2>Bienvenido a <span>Vikon</span></h2>
      <div class="vk-prose">${mode === "login" ? "Entra en tu cuenta." : "Crea tu cuenta para empezar."}</div>
      <div class="field"><label>Usuario</label>
        <input class="vk-input" id="au-user" autocomplete="username" placeholder="tu usuario" /></div>
      <div class="field"><label>Contraseña</label>
        <input class="vk-input" id="au-pass" type="password" placeholder="••••••"
          autocomplete="${mode === "login" ? "current-password" : "new-password"}" /></div>
      ${mode === "register" ? `
      <label class="consent">
        <input type="checkbox" id="au-consent" />
        <span>He leído los <a href="/static/terminos.html" target="_blank" rel="noopener">términos</a>
        y la <a href="/static/privacidad.html" target="_blank" rel="noopener">política de privacidad</a>,
        y consiento el tratamiento de mis <strong>datos de salud</strong> (pulso, peso, sueño)
        para calcular mi entrenamiento.</span>
      </label>
      <label class="consent">
        <input type="checkbox" id="au-consent-ai" />
        <span>Consiento que mis métricas se envíen a un proveedor de <strong>IA</strong>
        para redactar las explicaciones. <em>Opcional: Vikon funciona entero sin esto.</em></span>
      </label>` : ""}
      <div class="obact"><button class="vk-btn" id="au-submit">${mode === "login" ? "Entrar" : "Crear cuenta"}</button></div>
      <div class="ob-skip"><a id="au-toggle">${mode === "login"
        ? "¿No tienes cuenta? Crear una" : "¿Ya tienes cuenta? Entrar"}</a></div>
      ${mode === "login" ? `<div class="ob-skip" style="margin-top:6px">
        <a href="/static/privacidad.html" target="_blank" rel="noopener">Privacidad</a> ·
        <a href="/static/terminos.html" target="_blank" rel="noopener">Términos</a></div>` : ""}
      <div id="au-msg"></div>`;
    // El consentimiento de datos de salud (RGPD art. 9.2.a) tiene que ser una
    // acción afirmativa: casilla sin premarcar y botón bloqueado hasta marcarla.
    const consent = $("#au-consent");
    if (consent) {
      const submitBtn = $("#au-submit");
      submitBtn.disabled = true;
      consent.addEventListener("change", () => { submitBtn.disabled = !consent.checked; });
    }
    $("#au-submit").addEventListener("click", submit);
    $("#au-toggle").addEventListener("click", () => { mode = mode === "login" ? "register" : "login"; render(); });
    $("#au-pass").addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
  };
  render();
  $("#auth").classList.add("is-open");
  window.scrollTo(0, 0);
}

async function logout() {
  try { await api("/api/logout", { method: "POST" }); } catch (_) {}
  // PWA: registra el service worker para poder instalarla en el móvil.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () =>
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {}));
}

boot();
}

// --- Borrado de cuenta (RGPD art. 17) ---------------------------------------
// Dos confirmaciones a propósito: es irreversible y se lleva años de historial.
// La contraseña la valida el servidor; una cookie robada no debe bastar.
async function deleteAccount() {
  const msg = $("#account-msg");
  if (!confirm(
    "Vas a borrar tu cuenta y TODOS tus datos: entrenamientos, métricas, " +
    "planes y conversaciones.\n\nEsto no se puede deshacer. ¿Seguro?"
  )) return;
  const password = prompt("Escribe tu contraseña para confirmar el borrado:");
  if (!password) return;
  msg.textContent = "Borrando…";
  try {
    await api("/api/account", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    alert("Cuenta borrada. Gracias por haber usado Vikon.");
    location.reload();
  } catch (e) {
    msg.textContent = e.message || "No se pudo borrar la cuenta.";
  }
}

// --- Perfil / onboarding ----------------------------------------------------
const DAYS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"];

function profileFormHtml(p, onboarding) {
  const v = (x) => (x == null ? "" : x);
  const av = p.availability || {};
  const days = DAYS.map((d, i) => {
    const v = av[i] != null ? av[i] : 0;
    return `<div class="row"><span class="day">${d}</span>
      <input class="slider" type="range" min="0" max="360" step="15" id="av-${i}" value="${v}" />
      <span class="u" id="avl-${i}">${v ? hhmm(v) : "libre"}</span></div>`;
  }).join("");
  const opt = (val, label) => `<option value="${val}"${p.level === val ? " selected" : ""}>${label}</option>`;
  return `
    <h2>${onboarding ? "Bienvenido a <span>Vikon</span>" : "Perfil y disponibilidad"}</h2>
    <div class="vk-prose">${onboarding
      ? "Cuéntame lo básico para ajustar tu entrenamiento. Los datos físicos son opcionales."
      : "Edita tus datos y tu disponibilidad semanal."}</div>

    <div class="vk-key">Sobre ti</div>
    <div class="field"><label>Nombre</label><input class="vk-input" id="pf-name" value="${v(p.name)}" placeholder="Tu nombre" /></div>
    <div class="field"><label>Nivel deportivo</label>
      <select class="vk-input" id="pf-level"><option value="">—</option>
        ${opt("principiante", "Principiante")}${opt("intermedio", "Intermedio")}
        ${opt("avanzado", "Avanzado")}${opt("elite", "Élite")}
      </select></div>
    <div class="field"><label>FTP declarado (W)</label>
      <input class="vk-input" type="number" id="pf-ftp" value="${v(p.declared_ftp_w)}" placeholder="${p._est_ftp || "vatios"}" /></div>

    <div class="vk-key">Objetivo <span class="opt">· opcional</span></div>
    <div class="grid2">
      <div class="field"><label>Evento</label><input class="vk-input" id="pf-goal-name" value="${v(p.goal && p.goal.name)}" placeholder="Gran Fondo…" /></div>
      <div class="field"><label>Fecha</label><input class="vk-input" type="date" id="pf-goal-date" value="${v(p.goal && p.goal.date)}" /></div>
      <div class="field"><label>Tipo</label><select class="vk-input" id="pf-goal-kind">${kindOptions(p.goal && p.goal.kind)}</select></div>
      <div class="field"><label>Prioridad</label><select class="vk-input" id="pf-goal-prio">
        <option value="A">A · principal</option><option value="B">B</option><option value="C">C</option></select></div>
    </div>

    <div class="vk-key">Datos físicos <span class="opt">· opcional</span></div>
    <div class="grid2">
      <div class="field"><label>Sexo</label><select class="vk-input" id="pf-sex">
        <option value=""${!p.sex ? " selected" : ""}>—</option>
        <option value="M"${p.sex === "M" ? " selected" : ""}>Hombre</option>
        <option value="F"${p.sex === "F" ? " selected" : ""}>Mujer</option></select></div>
      <div class="field"><label>Nacimiento</label><input class="vk-input" type="date" id="pf-birth" value="${v(p.birthdate)}" /></div>
      <div class="field"><label>Altura (cm)</label><input class="vk-input" type="number" id="pf-height" value="${v(p.height_cm)}" /></div>
      <div class="field"><label>Peso (kg)</label><input class="vk-input" type="number" step="0.1" id="pf-weight" value="${v(p.weight_kg)}" /></div>
      <div class="field"><label>FC máx</label><input class="vk-input" type="number" id="pf-hrmax" value="${v(p.hr_max)}" /></div>
      <div class="field"><label>FC reposo</label><input class="vk-input" type="number" id="pf-hrrest" value="${v(p.hr_rest)}" /></div>
    </div>

    <div class="vk-key">Disponibilidad semanal</div>
    <div class="vk-prose" style="margin:-4px 2px 10px">Minutos que puedes entrenar cada día. Un día en 0 = descanso.</div>
    <div class="avail">${days}</div>
    <div class="avail-total">Total disponible: <b id="av-sum">0:00</b> h</div>

    <div class="vk-key">¿Cuánto quieres entrenar?</div>
    <div class="vk-prose" style="margin:-4px 2px 10px">Tener hueco no es querer entrenarlo.
      Esto es el presupuesto de la semana: el plan no lo pasará.</div>
    <div class="ckrow"><span>Objetivo semanal</span>
      <!-- max amplio de salida: el navegador RECORTA el value al max al
           parsear, y el tope real (la suma de tu disponibilidad) aún no se
           conoce aquí. recompute() lo estrecha en cuanto se monta. -->
      <input class="slider" type="range" id="pf-weekly" min="0" max="2520" step="15"
             value="${p.weekly_minutes_target ?? 480}" />
      <b id="pf-weeklyl">—</b></div>

    <div class="obact"><button class="vk-btn" id="pf-save">${onboarding ? "Empezar" : "Guardar"}</button></div>
    ${onboarding ? `<div class="ob-skip"><a id="pf-skip">Saltar por ahora</a></div>` : ""}
    <div id="ob-msg"></div>`;
}

async function showProfile({ onboarding }) {
  let p = {};
  try { p = await api("/api/profile"); } catch (_) { /* sin perfil todavía */ }
  if (onboarding && !p.declared_ftp_w) {
    try { const st = await api("/api/settings"); if (st.ftp) p._est_ftp = Math.round(st.ftp); } catch (_) {}
  }
  $("#ob-card").innerHTML = profileFormHtml(p, onboarding);
  $("#onboarding").classList.add("is-open");
  window.scrollTo(0, 0);
  const wk = $("#pf-weekly"), wkl = $("#pf-weeklyl");
  const recompute = () => {
    let sum = 0;
    for (let i = 0; i < 7; i++) {
      const v = Number($(`#av-${i}`).value || 0);
      sum += v;
      $(`#avl-${i}`).textContent = v ? hhmm(v) : "libre";
    }
    $("#av-sum").textContent = hhmm(sum);
    // El objetivo semanal no puede pedir más horas de las que tienes: su tope
    // es la suma de la disponibilidad, y se recorta si bajas algún día.
    wk.max = sum;
    if (Number(wk.value) > sum) wk.value = sum;
    wkl.textContent = hhmm(Number(wk.value)) + " h";
  };
  wk.addEventListener("input", () => { wkl.textContent = hhmm(Number(wk.value)) + " h"; });
  for (let i = 0; i < 7; i++) $(`#av-${i}`).addEventListener("input", recompute);
  recompute();
  $("#pf-save").addEventListener("click", () => submitProfile(onboarding));
  if (onboarding) $("#pf-skip").addEventListener("click", () => { $("#onboarding").classList.remove("is-open"); syncThenLoad(); });
}

async function submitProfile(onboarding) {
  const num = (id) => { const x = $(id).value.trim(); return x === "" ? null : Number(x); };
  const str = (id) => { const x = $(id).value.trim(); return x === "" ? null : x; };
  const availability = {};
  for (let i = 0; i < 7; i++) { const x = $(`#av-${i}`).value.trim(); availability[i] = x === "" ? 0 : Number(x); }
  const payload = {
    name: str("#pf-name"), level: str("#pf-level"), declared_ftp_w: num("#pf-ftp"),
    sex: str("#pf-sex"), birthdate: str("#pf-birth"), height_cm: num("#pf-height"),
    weight_kg: num("#pf-weight"), hr_max: num("#pf-hrmax"), hr_rest: num("#pf-hrrest"),
    availability, weekly_minutes_target: num("#pf-weekly"),
    goal_name: str("#pf-goal-name"), goal_date: str("#pf-goal-date"),
    goal_kind: str("#pf-goal-kind"), goal_priority: $("#pf-goal-prio").value || "A",
  };
  const btn = $("#pf-save"); btn.disabled = true;
  try {
    await api("/api/profile", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    $("#onboarding").classList.remove("is-open");
    horizonLoaded = false;
    if (onboarding) { syncThenLoad(); }
    else { loadHome(); renderSettings(); }
  } catch (e) { $("#ob-msg").textContent = e.detail || "No se pudo guardar."; btn.disabled = false; }
}

// --- Chat -------------------------------------------------------------------
function addMsg(text, cls) {
  const el = document.createElement("div");
  el.className = "vk-msg vk-msg--" + cls; el.textContent = text;
  $("#chat-log").appendChild(el);
  el.scrollIntoView({ behavior: "smooth", block: "end" });
}
async function sendChat() {
  const input = $("#chat-text"), msg = input.value.trim();
  if (!msg) return;
  addMsg(msg, "user"); input.value = "";
  const pending = document.createElement("div");
  pending.className = "vk-msg vk-msg--bot"; pending.textContent = "…";
  $("#chat-log").appendChild(pending);
  // Streaming: la respuesta se va escribiendo según llega (SSE), en vez de
  // esperar al mensaje entero. Si algo falla, cae al modo clásico.
  try {
    const resp = await fetch("/api/chat/stream", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: msg }),
    });
    if (!resp.ok) {                          // di la VERDAD, no un genérico
      let detail = `Error ${resp.status}`;
      try { detail = (await resp.json()).detail || detail; } catch (_) {}
      if (resp.status === 401) detail = "Tu sesión ha caducado. Vuelve a entrar.";
      pending.textContent = detail;
      return;
    }
    if (!resp.body) throw new Error("sin stream");   // navegador sin streaming
    const reader = resp.body.getReader(), dec = new TextDecoder();
    let buf = "", started = false;
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const events = buf.split("\n\n");
      buf = events.pop() || "";                 // el último puede venir a medias
      for (const ev of events) {
        const type = (ev.match(/^event: (.+)$/m) || [])[1];
        const raw = (ev.match(/^data: (.*)$/m) || [])[1];
        if (!type || raw === undefined) continue;
        let data; try { data = JSON.parse(raw); } catch { continue; }
        if (type === "meta") {
          const logged = Object.keys(data.logged || {});
          if (logged.length) addMsg("✓ registrado: " + logged.join(", "), "hint");
          // Si contestaste el check-in por chat, no hay que volver a preguntarlo.
          if (logged.includes("sleep_hours") || logged.includes("feel")) checkinPending = false;
          const changed = Object.keys(data.changed || {});
          if (changed.length) {
            addMsg("✓ actualizado: " + changed.join(", "), "hint");
            horizonLoaded = false; activitiesLoaded = false;
          }
          const hint = [];
          if (data.intent && data.intent.minutes != null) hint.push(hhmm(data.intent.minutes) + " h");
          if (data.intent && data.intent.readiness) hint.push(data.intent.readiness);
          if (hint.length) addMsg("interpretado: " + hint.join(", "), "hint");
          $("#chat-log").appendChild(pending);   // el globo vuelve al final
        } else if (type === "chunk") {
          if (!started) { pending.textContent = ""; started = true; }
          pending.textContent += data.text;
          pending.scrollIntoView({ behavior: "smooth", block: "end" });
        } else if (type === "error") {
          pending.textContent = data.detail || "No pude responder.";
        }
      }
    }
    if (!started && pending.textContent === "…") pending.textContent = "No pude responder.";
    loadHome();
  } catch (_) {
    // Plan B: si el streaming falla (navegador viejo, proxy que lo corta), se
    // pide la respuesta completa por el endpoint clásico.
    try {
      const r = await api("/api/chat", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: msg }),
      });
      pending.textContent = r.text;
      loadHome();
    } catch (e2) {
      pending.textContent = e2.detail || "No pude responder. Mira la consola del servidor.";
    }
  }
}

// Saludo matinal: si aún no has hecho el check-in, el chat lo pregunta al
// abrirlo. No es un push (el servidor corre en tu PC y puede estar apagado):
// es un recibimiento fiable, sin depender de nada externo.
let checkinPending = false;
let greeted = false;

function maybeGreet() {
  if (greeted || !checkinPending) return;
  greeted = true;
  const h = new Date().getHours();
  const when = h < 12 ? "Buenos días" : h < 21 ? "Buenas" : "Buenas noches";
  addMsg(
    `${when}. Antes de decidir el entreno de hoy: ¿cuántas horas has dormido `
    + "y cómo te sientes del 1 al 10?", "bot",
  );
  addMsg('Puedes contestarme aquí (ej: "dormí 7h y me siento un 6") '
    + "o usar los deslizadores en Progreso.", "hint");
}

// --- Navegación -------------------------------------------------------------
function show(view) {
  ["home", "progress", "horizon", "activities", "chat", "settings"]
    .forEach((v) => { $(`#${v}-view`).style.display = v === view ? "block" : "none"; });
  document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $("#gear").classList.toggle("active", view === "settings");
  const isChat = view === "chat";
  $("#chat-input").style.display = isChat ? "flex" : "none";
  $("#quick").style.display = isChat ? "flex" : "none";
  if (isChat) { $("#chat-text").focus(); maybeGreet(); }
  if (view === "horizon") loadHorizon();
  if (view === "progress") renderProgress();
  if (view === "activities") loadActivities();
  if (view === "settings") renderSettings();
}

let horizonLoaded = false;
async function loadHome() {
  if (!$("#home-content").dataset.ready) $("#home-content").innerHTML = SKELETON.home;
  try {
    renderHome(await api("/api/state"));
    $("#home-content").dataset.ready = "1";
  }
  catch (e) { $("#home-content").innerHTML = `<div class="vk-empty">${e.detail || "Error cargando el estado."}</div>`; }
}
async function loadHorizon() {
  if (horizonLoaded) return;
  $("#horizon-content").innerHTML = SKELETON.rows;
  try { renderHorizon(await api("/api/horizon?days=7")); horizonLoaded = true; }
  catch (e) { $("#horizon-content").innerHTML = `<div class="vk-empty">${e.detail || "Error."}</div>`; }
}
async function syncThenLoad() {
  loadHome();                       // pinta ya con lo que haya en BD (no bloquea)
  try {                             // sincroniza en 2º plano, con timeout: nunca cuelga
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 12000);
    const r = await api("/api/refresh", { method: "POST", signal: ctrl.signal });
    clearTimeout(t);
    if (r.new > 0) { horizonLoaded = false; activitiesLoaded = false; loadHome(); }  // datos nuevos
  } catch (_) { /* sin conexión/credenciales/timeout: seguimos con lo cargado */ }
}

// --- Init -------------------------------------------------------------------
paintIcons();
$("#today").textContent = new Date().toLocaleDateString("es-ES", { weekday: "long", day: "numeric", month: "short" });
document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));
$("#gear").innerHTML = icon("settings", 20);
$("#gear").addEventListener("click", () => show("settings"));
$("#chat-send").addEventListener("click", sendChat);
$("#chat-text").addEventListener("keydown", (e) => { if (e.key === "Enter") sendChat(); });
document.querySelectorAll("#quick button").forEach((b) => b.addEventListener("click", () => {
  if (b.dataset.log) {
    $("#chat-text").placeholder = "Ej: peso 72, dormí 6h, me siento un 4/10";
    $("#chat-text").focus();
  } else {
    $("#chat-text").value = b.dataset.q; sendChat();
  }
}));

// Arranque: (1) ¿hace falta iniciar sesión? (2) ¿falta onboarding? si no, carga.
async function boot() {
  let me = null;
  try { me = await api("/api/me"); } catch (_) { /* sin backend: intenta cargar igual */ }
  if (me && me.auth_required && !me.authenticated) { showAuth(me.has_users); return; }
  let prof = null;
  try { prof = await api("/api/profile"); } catch (_) { /* sin atleta: sigue igual */ }
  if (prof && !prof.onboarded) showProfile({ onboarding: true });
  else syncThenLoad();
  // ¿Falta el check-in de hoy? Entonces el chat lo preguntará al abrirlo.
  try { checkinPending = (await api("/api/checkin")).pending; } catch (_) { /* da igual */ }
  handleStravaReturn();          // ¿venimos de autorizar en Strava?
}
// PWA: registra el service worker para poder instalarla en el móvil.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () =>
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {}));
}

boot();
