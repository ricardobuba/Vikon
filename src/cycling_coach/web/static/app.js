// Vikon — frontend mobile-first. Consume la API JSON; el motor decide, aquí solo pintamos.
const $ = (s) => document.querySelector(s);
const api = (p, opts) => fetch(p, opts).then((r) => r.ok ? r.json() : r.json().then((e) => Promise.reject(e)));
const fmt = (v, d = 0) => (v == null ? "—" : Number(v).toFixed(d));
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
  return `<div class="block ${zoneFor(t)}"><span>${label}</span><span class="w">${w}</span></div>`;
}

// Medidor de forma: barra de color con marcador en tu TSB (zonas personalizadas).
function formGauge(s) {
  if (s.tsb == null) return "";
  const th = s.thresholds || {};
  const lo = (th.recovery != null ? th.recovery : -25) - 8;
  const hi = (th.fresh != null ? th.fresh : 15) + 8;
  const pos = Math.max(2, Math.min(98, (s.tsb - lo) / (hi - lo) * 100));
  const label = s.form_label || "";
  const color = {
    "Muy fatigado": "var(--red)", "Fatigado": "var(--coral)", "Neutro": "var(--amber)",
    "Fresco": "var(--teal)", "Muy fresco": "var(--electric)",
  }[label] || "var(--text)";
  return `<div class="card">
    <div class="form-head"><h3>Tu forma</h3><span class="form-status" style="color:${color}">${label} · ${signed(s.tsb)}</span></div>
    <div class="gauge"><div class="gauge-marker" style="left:${pos}%"></div></div>
    <div class="gauge-labels"><span>Fatigado</span><span>Neutro</span><span>Fresco</span></div>
  </div>`;
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
    return `<div class="why-note"><b>${head.trim()}</b>${body ? `<span>${body}</span>` : ""}</div>`;
  }).join("");
  return `<div class="card why"><h3>Por qué este entrenamiento</h3>
    <p>${main}</p>${chips ? `<div class="why-notes">${chips}</div>` : ""}</div>`;
}

// Esqueletos: mientras llegan los datos se ve la FORMA de la pantalla.
const SKELETON = {
  home: `<div class="sk sk-hero"></div>
    <div class="sk-stats">${'<div class="sk sk-stat"></div>'.repeat(4)}</div>
    <div class="sk sk-card"></div>`,
  rows: `${'<div class="sk sk-row"></div>'.repeat(6)}`,
  cards: `<div class="sk sk-card"></div><div class="sk sk-card"></div>`,
};

// --- Pantalla HOY -----------------------------------------------------------
function renderHome(s) {
  const p = s.plan;
  const tsbClass = s.tsb == null ? "" : s.tsb >= 0 ? "pos" : "neg";
  let goal = "";
  if (s.goal_date) goal = `<div class="goal">
    <span class="goal-ic">${icon("target", 20)}</span>
    <div class="goal-txt"><b>${s.goal_name || "Evento"}</b><span>faltan ${s.days_to_event} días · fase ${s.phase}</span></div>
    <span class="goal-days">${s.days_to_event}<i>días</i></span>
  </div>`;
  const trained = s.trained_today
    ? `<div class="pill-ok"><span class="pill-ic">${icon("check", 18)}</span>Ya entrenaste hoy · ${hhmm(s.trained_minutes)} h</div>` : "";
  const planLabel = s.trained_today ? "Mañana" : "Plan de hoy";
  let planHtml = `<div class="loading">No hay plan. Falta el FTP.</div>`;
  if (p) {
    const adjust = p.aspired ? `<div class="badge-adjust">rebajado desde ${p.aspired}</div>` : "";
    const blocks = (p.targets || []).map(blockHtml).join("");
    planHtml = `${trained}
      <div class="hero"><div class="hero-inner">
        <div class="label">${planLabel}</div>
        <div class="session">${p.session}</div>
        <div class="objective">${p.objective.replace("_", " ")} · <span class="duration">${hhmm(p.minutes)} h</span></div>
        ${adjust}<div class="blocks">${blocks}</div>
      </div></div>`;
  }
  $("#home-content").innerHTML = `${planHtml}
    <div class="stats">
      <div class="stat ${tsbClass}"><span class="num">${signed(s.tsb)}</span><span class="k">FORMA</span></div>
      <div class="stat"><span class="num">${fmt(s.ctl)}</span><span class="k">FITNESS</span></div>
      <div class="stat"><span class="num">${fmt(s.cri)}</span><span class="k">CRI</span></div>
      <div class="stat"><span class="num">${fmt(s.ftp)}</span><span class="k">FTP W</span></div>
    </div>
    ${formGauge(s)}
    ${goal}
    <div class="card"><h3>Forma y predicción</h3><div class="sub">Fitness (CTL) y frescura (TSB) · 60 días + próximos 7 (punteado). Toca la gráfica.</div><div id="form-svg" class="loading" style="padding:20px">…</div></div>
    ${p ? whyCard(p.rationale) : ""}`;
  // gráfica de forma + predicción (interactiva)
  api("/api/form-forecast?past=60&future=7").then((t) => {
    if (!t.length) { $("#form-svg").innerHTML = `<div class="sub">sin datos</div>`; return; }
    let split = t.findIndex((d) => d.projected);
    split = split < 0 ? t.length - 1 : split - 1;
    mountChart($("#form-svg"), {
      dates: labelDates(t.map((d) => d.day)),
      series: [
        { vals: t.map((d) => d.ctl), color: "#12A9E0", name: "Fitness (CTL)", fill: true },
        { vals: t.map((d) => d.tsb), color: "#1F6BEC", name: "Forma (TSB)" },
      ],
      zeroLine: true, splitIndex: split,
    });
  }).catch(() => { $("#form-svg").innerHTML = `<div class="sub">—</div>`; });
}

// --- Pantalla ACTIVIDADES ---------------------------------------------------
const ZONE_OF_IF = (i) => (i == null ? "z2" : i < 0.60 ? "z1" : i < 0.76 ? "z2"
  : i < 0.88 ? "z3" : i < 1.00 ? "z4" : "z5");

function renderActivities(list) {
  const box = $("#activities-content");
  if (!list.length) { box.innerHTML = `<div class="loading">Aún no hay entrenamientos.</div>`; return; }
  box.innerHTML = list.map((a, i) => {
    const stat = (v, k, u = "") => v == null ? "" : `<div class="am"><b>${v}${u}</b><span>${k}</span></div>`;
    return `<div class="acard" data-i="${i}">
      <div class="ahead">
        <span class="adot ${ZONE_OF_IF(a.intensity)}"></span>
        <div class="atitle"><b>${a.name || "Entrenamiento"}</b>
          <span>${new Date(a.day).toLocaleDateString("es-ES", { weekday: "long", day: "numeric", month: "short" })}${a.session_label ? " · " + a.session_label : ""}</span></div>
        <span class="chev">${icon("chevron", 16)}</span>
      </div>
      <div class="amets">
        ${stat(hhmm(a.minutes), "tiempo")}
        ${stat(a.distance_km, "km")}
        ${stat(a.np_w, "W norm.")}
        ${stat(a.tss, "TSS")}
      </div>
      <div class="adetail" style="display:none" data-id="${a.id}">
        <div class="loading" style="padding:12px">Cargando…</div>
      </div>
    </div>`;
  }).join("");
  box.querySelectorAll(".acard").forEach((c) => {
    c.querySelector(".ahead").addEventListener("click", async () => {
      const d = c.querySelector(".adetail");
      const open = d.style.display !== "none";
      d.style.display = open ? "none" : "block";
      c.classList.toggle("open", !open);
      if (!open && !d.dataset.loaded) {        // detalle bajo demanda
        d.dataset.loaded = "1";
        try { d.innerHTML = activityDetailHtml(await api(`/api/activity/${d.dataset.id}`)); }
        catch (e) { d.innerHTML = `<div class="loading">${e.detail || "Error."}</div>`; }
      }
    });
  });
}

// Ficha del entrenamiento: qué fue realmente + métricas + zonas + series.
function activityDetailHtml(d) {
  const stat = (v, k, u = "") => v == null ? "" : `<div class="am"><b>${v}${u}</b><span>${k}</span></div>`;
  const totalZ = (d.zones || []).reduce((s, z) => s + z.seconds, 0) || 1;
  const zbar = (d.zones || []).map((z) => {
    const cls = "z" + (z.zone.match(/Z(\d)/) || [0, 2])[1];
    return `<div class="zrow"><span class="zl">${z.zone}</span>
      <span class="ztrack"><span class="zfill ${cls}" style="width:${(z.seconds / totalZ * 100).toFixed(1)}%"></span></span>
      <span class="zv">${hhmm(z.seconds / 60)}</span></div>`;
  }).join("");
  const reps = (d.intervals || []).map((v, i) =>
    `<div class="irow"><span>#${i + 1}</span><b>${hhmm(v.seconds / 60)}</b>
      <span>${v.avg_w} W</span><span class="ipct">${v.pct_ftp}% FTP</span></div>`).join("");
  return `
    ${d.session_label ? `<div class="skind">${d.session_label}${d.detected ? ` · ${d.detected}` : ""}</div>` : ""}
    <p class="atext">${d.text}</p>
    <div class="amets sub2">
      ${stat(d.avg_power_w, "W media")}${stat(d.np_w, "W norm.")}
      ${stat(d.max_power_w, "W máx")}${stat(d.avg_hr, "ppm medio")}
      ${stat(d.max_hr, "ppm máx")}${stat(d.avg_cadence, "rpm")}
      ${stat(d.elevation_m, "m desnivel")}${stat(d.intensity, "IF")}
      ${stat(d.kilojoules, "kJ")}${stat(d.tss, "TSS")}
    </div>
    ${zbar ? `<div class="ssec">Tiempo en zonas</div><div class="zones">${zbar}</div>` : ""}
    ${reps ? `<div class="ssec">Series detectadas</div><div class="ivals">${reps}</div>` : ""}`;
}

let activitiesLoaded = false;
async function loadActivities() {
  if (activitiesLoaded) return;
  $("#activities-content").innerHTML = SKELETON.rows;
  try { renderActivities(await api("/api/activities?limit=30")); activitiesLoaded = true; }
  catch (e) { $("#activities-content").innerHTML = `<div class="loading">${e.detail || "Error."}</div>`; }
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
  // Check-in diario: sueño + sensación. Es la única entrada de recuperación
  // que tenemos sin wearable, y alimenta el CRI.
  if (chk) html += checkinCard(chk);
  // Cumplimiento del plan: lo prescrito vs lo hecho (cierra el bucle).
  if (comp) {
    if (comp.rate == null) {
      html += `<div class="card"><h3>Cumplimiento del plan</h3>
        <div class="sub">${comp.note}</div></div>`;
    } else {
      const ST = {
        cumplido: ["ok", "Cumplido"], descanso_ok: ["ok", "Descanso"],
        más: ["warn", "Más carga"], menos: ["warn", "Menos carga"],
        distinto: ["warn", "Otra sesión"], no_hecho: ["bad", "No hecho"],
        extra: ["info", "Extra"],
      };
      const rows = comp.days.slice(-10).reverse().map((d) => {
        const [cls, label] = ST[d.status] || ["info", d.status];
        return `<div class="cmrow"><span class="cmd">${shortDate(d.day)}</span>
          <span class="cmtag ${cls}">${label}</span>
          <span class="cmn">${d.note}</span></div>`;
      }).join("");
      const pct = Math.round(comp.rate * 100);
      html += `<div class="card"><h3>Cumplimiento del plan</h3>
        <div class="sub">Últimos 28 días · lo prescrito vs lo que hiciste</div>
        <div class="cmhead">
          <div><b class="cmbig">${pct}%</b><span>seguido</span></div>
          <div><b class="cmbig">${comp.tss_done}</b><span>TSS hecho</span></div>
          <div><b class="cmbig">${comp.tss_planned}</b><span>TSS previsto</span></div>
        </div>
        <div class="cmbar"><span style="width:${pct}%"></span></div>
        <div class="cmlist">${rows}</div></div>`;
    }
  }
  // Tu motor: número actual (FTP/CP) — sin gráfica.
  if (ftp.length) {
    const cur = ftp[ftp.length - 1];
    html += `<div class="card"><h3>Tu motor</h3>
      <div class="motor-row">
        <div class="motor"><span class="mk">FTP</span><span class="mv">${cur.ftp}<i>W</i></span></div>
        <div class="motor"><span class="mk">CP</span><span class="mv">${cur.cp}<i>W</i></span></div>
      </div></div>`;
  }
  // Curva de potencia (real vs modelo, interactiva) + coherencia del CP
  if (pc) {
    html += `<div class="card"><h3>Curva de potencia</h3>
      <div class="sub">Tu mejor real (120 d) vs modelo CP/W' · toca la gráfica</div>
      <div id="pc-chart"></div>
      ${legend([{ color: "#12A9E0", label: "Real (potencia máx.)" }, { color: "#1F6BEC", label: "Modelo CP/W'" }])}
      ${pc.verdict ? `<div class="verdict ${pc.coherent ? "ok" : "warn"}">${pc.verdict}</div>` : ""}</div>`;
  }
  box.innerHTML = html || `<div class="loading">Sin datos de potencia todavía.</div>`;
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
  return `<div class="card" id="checkin">
    <h3>¿Cómo has dormido?</h3>
    <div class="sub">${done
      ? "Registrado hoy. Puedes corregirlo si quieres."
      : "Sin reloj no se puede medir el sueño, así que se pregunta. Tu propia percepción es una señal válida — alimenta la Recuperación del CRI."}</div>
    <div class="ckrow"><span>Horas dormidas</span>
      <input type="range" id="ck-sleep" min="0" max="12" step="0.25" value="${sleep}" />
      <b id="ck-sleepl">${hhmm(sleep * 60)} h</b></div>
    <div class="ckrow"><span>Cómo te sientes</span>
      <input type="range" id="ck-feel" min="1" max="10" step="1" value="${feel}" />
      <b id="ck-feell">${feel}/10 · ${FEEL_WORDS[feel]}</b></div>
    <button id="ck-save">${done ? "Actualizar" : "Guardar check-in"}</button>
    <span class="ckmsg" id="ck-msg"></span>
  </div>`;
}

function wireCheckin() {
  const s = $("#ck-sleep"), f = $("#ck-feel"), msg = $("#ck-msg");
  if (!s) return;
  s.addEventListener("input", () => { $("#ck-sleepl").textContent = hhmm(+s.value * 60) + " h"; });
  f.addEventListener("input", () => {
    $("#ck-feell").textContent = `${f.value}/10 · ${FEEL_WORDS[f.value]}`;
  });
  $("#ck-save").addEventListener("click", async () => {
    msg.textContent = "Guardando…"; msg.className = "ckmsg";
    try {
      await api("/api/checkin", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sleep_hours: +s.value, feel: +f.value }),
      });
      msg.textContent = "Guardado ✓"; msg.className = "ckmsg ok";
      checkinPending = false;
      loadHome();                    // el CRI cambia: refresca la portada
    } catch (e) {
      msg.textContent = e.detail || "No se pudo guardar."; msg.className = "ckmsg bad";
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
  if (!days.length) { $("#horizon-content").innerHTML = `<div class="loading">Sin datos.</div>`; return; }
  const names = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];
  $("#horizon-content").innerHTML = days.map((h, i) => {
    const d = i === 0 ? "HOY" : names[new Date(h.day).getDay()];
    const blocks = (h.targets && h.targets.length)
      ? h.targets.map(blockHtml).join("")
      : `<div class="empty">${h.objective === "rest" ? "Día libre — sin sesión." : ""}</div>`;
    const why = (h.rationale || "").replace(/\[/g, "· ").replace(/\]/g, "");
    return `<div class="hitem" data-day="${h.day}">
      <div class="hrow"><span class="d">${d}</span>
        <span class="o">${h.objective.replace("_", " ")}<br><span style="color:var(--muted);font-size:12px">${h.session}</span></span>
        <span class="t"><b class="hdur">${h.minutes ? hhmm(h.minutes) + " h" : "libre"}</b><br>${h.tss} TSS</span>
        <span class="chev">${icon("chevron", 14)}</span></div>
      <div class="hdetail" style="display:none">
        ${h.description ? `<p class="hdesc">${h.description}</p>` : ""}
        <div class="hstats">
          <div class="hs"><b>${hhmm(h.minutes)}</b><span>duración</span></div>
          <div class="hs"><b>${h.tss}</b><span>TSS</span></div>
          <div class="hs"><b>${h.intensity != null ? h.intensity.toFixed(2) : "—"}</b><span>IF</span></div>
          <div class="hs"><b>${signed(h.tsb)}</b><span>forma ese día</span></div>
          <div class="hs"><b>${h.ctl}</b><span>fitness</span></div>
          <div class="hs"><b>${PHASE_ES[h.phase] || h.phase}</b><span>fase</span></div>
        </div>
        ${blocks}
        ${why ? `<div class="hwhy"><b>Por qué:</b> ${why}</div>` : ""}
        <div class="dayctl">
          <div class="dayrow"><span>Tiempo ese día</span>
            <input type="range" class="dmin" min="0" max="360" step="15" value="${h.minutes || 0}" />
            <b class="dminl">${h.minutes ? hhmm(h.minutes) : "libre"}</b></div>
          <div class="dayrow"><span>Entrenamiento</span>
            <select class="dobj">${OBJECTIVES.map(([v, l]) =>
              `<option value="${v}">${l}</option>`).join("")}</select></div>
          <button class="dsave">Guardar este día</button>
          <span class="dmsg"></span>
        </div>
      </div>
    </div>`;
  }).join("");

  $("#horizon-content").querySelectorAll(".hitem").forEach((item) => {
    const row = item.querySelector(".hrow"), det = item.querySelector(".hdetail");
    row.addEventListener("click", async () => {
      const open = det.style.display !== "none";
      det.style.display = open ? "none" : "block";
      row.classList.toggle("open", !open);
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
  catch (e) { box.innerHTML = `<div class="loading">${e.detail || "Error cargando ajustes."}</div>`; return; }

  const wp = s.w_prime != null ? (s.w_prime / 1000).toFixed(1) + " kJ" : "—";
  const goal = s.goal
    ? `<div class="setrow"><span>${s.goal.name || "Evento"}</span><span class="v">${shortDate(s.goal.date)} · faltan ${s.goal.days_to} d</span></div>`
    : `<div class="sub">Sin objetivo. Añade uno para activar la periodización (fase/taper).</div>`;
  const llmHost = (() => { try { return new URL(s.llm.base_url).host; } catch { return s.llm.base_url; } })();
  const st = s.strava || { connected: false, can_connect: false };

  box.innerHTML = `
    <div class="card"><h3>Perfil y disponibilidad</h3>
      <div class="sub">Tus datos y cuánto tiempo tienes cada día. El plan encaja las sesiones en tu disponibilidad.</div>
      <button id="edit-profile" class="btn-full" style="margin-top:10px">Editar perfil y disponibilidad</button>
    </div>

    <div class="card"><h3>Tu motor</h3>
      <div class="setrow"><span>FTP</span><span class="v">${fmt(s.ftp)} W</span></div>
      <div class="setrow"><span>CP (potencia crítica)</span><span class="v">${fmt(s.cp)} W</span></div>
      <div class="setrow"><span>W′ (reserva anaeróbica)</span><span class="v">${wp}</span></div>
      <div class="sub" style="margin-top:8px">Se recalibra solo cuando entran entrenamientos nuevos con potencia.</div>
      <button id="calib-now" class="btn-full">Recalibrar ahora</button>
      <div id="calib-msg" class="sub"></div>
    </div>

    <div class="card"><h3>Objetivo</h3>
      ${goal}
      <div class="goalform">
        <input id="goal-name" placeholder="Nombre (p. ej. Gran Fondo de León)" autocomplete="off" />
        <input id="goal-date" type="date" />
        <select id="goal-kind">${kindOptions()}</select>
        <select id="goal-prio"><option value="A">A · principal</option><option value="B">B</option><option value="C">C</option></select>
        <button id="goal-save">Guardar objetivo</button>
      </div>
      <div id="goal-msg" class="sub"></div>
    </div>

    <div class="card"><h3>Datos</h3>
      <div class="setrow"><span>Strava</span><span class="v">${st.connected
        ? '<span class="stat-dot ok"></span>conectado' : '<span class="stat-dot warn"></span>sin conectar'}</span></div>
      <div class="setrow"><span>Actividades importadas</span><span class="v">${s.activities}</span></div>
      <div class="setrow"><span>Última actividad</span><span class="v">${s.last_activity ? shortDate(s.last_activity) : "—"}</span></div>
      ${st.connected
        ? `<div class="sub" style="margin:8px 0">Los entrenamientos entran solos desde Strava. Puedes forzar una sincronización ahora:</div>
           <button id="sync-now" class="btn-full">${icon("refresh", 18)} Sincronizar con Strava</button>
           <div id="sync-msg" class="sub"></div>
           <button id="strava-off" class="btn-full" style="margin-top:8px;background:var(--card-2);color:var(--muted)">Desconectar Strava</button>`
        : st.can_connect
          ? `<div class="sub" style="margin:8px 0">Este perfil aún no tiene Strava. Conéctalo para que tus entrenamientos entren solos.</div>
             <button id="strava-on" class="btn-full">Conectar mi Strava</button>`
          : `<div class="sub" style="margin:8px 0">Faltan las credenciales de Strava en el archivo <code>.env</code> del servidor.</div>`}
      <div id="strava-msg" class="sub"></div>
    </div>

    <div class="card"><h3>Vikon IA</h3>
      <div class="setrow"><span>Estado</span><span class="v">${s.llm.configured
        ? '<span class="stat-dot ok"></span>conectada' : '<span class="stat-dot warn"></span>sin clave'}</span></div>
      <div class="setrow"><span>Modelo</span><span class="v">${s.llm.model}</span></div>
      <div class="setrow"><span>Proveedor</span><span class="v">${llmHost}</span></div>
      ${s.llm.configured ? "" : `<div class="sub" style="margin-top:8px">Añade tu clave en el archivo <code>.env</code> para activar el chat.</div>`}
    </div>

    <div class="card"><h3>Cuenta</h3>
      <div class="sub">Vikon · entrenador de ciclismo con gemelo digital. El motor decide; la IA explica.</div>
      <button id="logout-btn" class="btn-full" style="margin-top:12px;background:var(--card-2);color:var(--text)">Cerrar sesión</button>
    </div>`;

  $("#edit-profile").addEventListener("click", () => showProfile({ onboarding: false }));
  $("#logout-btn").addEventListener("click", logout);
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
  el.className = "toast " + cls;
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
    try {
      await api("/api/" + mode, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      $("#auth").style.display = "none";
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
      <div class="lead">${mode === "login" ? "Entra en tu cuenta." : "Crea tu cuenta para empezar."}</div>
      <div class="field"><label>Usuario</label>
        <input id="au-user" autocomplete="username" placeholder="tu usuario" /></div>
      <div class="field"><label>Contraseña</label>
        <input id="au-pass" type="password" placeholder="••••••"
          autocomplete="${mode === "login" ? "current-password" : "new-password"}" /></div>
      <div class="ob-actions"><button id="au-submit">${mode === "login" ? "Entrar" : "Crear cuenta"}</button></div>
      <div class="ob-skip"><a id="au-toggle">${mode === "login"
        ? "¿No tienes cuenta? Crear una" : "¿Ya tienes cuenta? Entrar"}</a></div>
      <div id="au-msg"></div>`;
    $("#au-submit").addEventListener("click", submit);
    $("#au-toggle").addEventListener("click", () => { mode = mode === "login" ? "register" : "login"; render(); });
    $("#au-pass").addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });
  };
  render();
  $("#auth").style.display = "flex";
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

// --- Perfil / onboarding ----------------------------------------------------
const DAYS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"];

function profileFormHtml(p, onboarding) {
  const v = (x) => (x == null ? "" : x);
  const av = p.availability || {};
  const days = DAYS.map((d, i) => {
    const v = av[i] != null ? av[i] : 0;
    return `<div class="row"><span class="day">${d}</span>
      <input type="range" min="0" max="360" step="15" id="av-${i}" value="${v}" />
      <span class="u" id="avl-${i}">${v ? hhmm(v) : "libre"}</span></div>`;
  }).join("");
  const opt = (val, label) => `<option value="${val}"${p.level === val ? " selected" : ""}>${label}</option>`;
  return `
    <h2>${onboarding ? "Bienvenido a <span>Vikon</span>" : "Perfil y disponibilidad"}</h2>
    <div class="lead">${onboarding
      ? "Cuéntame lo básico para ajustar tu entrenamiento. Los datos físicos son opcionales."
      : "Edita tus datos y tu disponibilidad semanal."}</div>

    <div class="ob-sec">Sobre ti</div>
    <div class="field"><label>Nombre</label><input id="pf-name" value="${v(p.name)}" placeholder="Tu nombre" /></div>
    <div class="field"><label>Nivel deportivo</label>
      <select id="pf-level"><option value="">—</option>
        ${opt("principiante", "Principiante")}${opt("intermedio", "Intermedio")}
        ${opt("avanzado", "Avanzado")}${opt("elite", "Élite")}
      </select></div>
    <div class="field"><label>FTP declarado (W)</label>
      <input type="number" id="pf-ftp" value="${v(p.declared_ftp_w)}" placeholder="${p._est_ftp || "vatios"}" /></div>

    <div class="ob-sec">Objetivo <span class="opt">· opcional</span></div>
    <div class="grid2">
      <div class="field"><label>Evento</label><input id="pf-goal-name" value="${v(p.goal && p.goal.name)}" placeholder="Gran Fondo…" /></div>
      <div class="field"><label>Fecha</label><input type="date" id="pf-goal-date" value="${v(p.goal && p.goal.date)}" /></div>
      <div class="field"><label>Tipo</label><select id="pf-goal-kind">${kindOptions(p.goal && p.goal.kind)}</select></div>
      <div class="field"><label>Prioridad</label><select id="pf-goal-prio">
        <option value="A">A · principal</option><option value="B">B</option><option value="C">C</option></select></div>
    </div>

    <div class="ob-sec">Datos físicos <span class="opt">· opcional</span></div>
    <div class="grid2">
      <div class="field"><label>Sexo</label><select id="pf-sex">
        <option value=""${!p.sex ? " selected" : ""}>—</option>
        <option value="M"${p.sex === "M" ? " selected" : ""}>Hombre</option>
        <option value="F"${p.sex === "F" ? " selected" : ""}>Mujer</option></select></div>
      <div class="field"><label>Nacimiento</label><input type="date" id="pf-birth" value="${v(p.birthdate)}" /></div>
      <div class="field"><label>Altura (cm)</label><input type="number" id="pf-height" value="${v(p.height_cm)}" /></div>
      <div class="field"><label>Peso (kg)</label><input type="number" step="0.1" id="pf-weight" value="${v(p.weight_kg)}" /></div>
      <div class="field"><label>FC máx</label><input type="number" id="pf-hrmax" value="${v(p.hr_max)}" /></div>
      <div class="field"><label>FC reposo</label><input type="number" id="pf-hrrest" value="${v(p.hr_rest)}" /></div>
    </div>

    <div class="ob-sec">Disponibilidad semanal</div>
    <div class="lead" style="margin:-4px 2px 10px">Minutos que puedes entrenar cada día. Un día en 0 = descanso.</div>
    <div class="avail">${days}</div>
    <div class="avail-total">Total semanal: <b id="av-sum">0:00</b> h</div>

    <div class="ob-actions"><button id="pf-save">${onboarding ? "Empezar" : "Guardar"}</button></div>
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
  $("#onboarding").style.display = "flex";
  window.scrollTo(0, 0);
  const recompute = () => {
    let sum = 0;
    for (let i = 0; i < 7; i++) {
      const v = Number($(`#av-${i}`).value || 0);
      sum += v;
      $(`#avl-${i}`).textContent = v ? hhmm(v) : "libre";
    }
    $("#av-sum").textContent = hhmm(sum);
  };
  for (let i = 0; i < 7; i++) $(`#av-${i}`).addEventListener("input", recompute);
  recompute();
  $("#pf-save").addEventListener("click", () => submitProfile(onboarding));
  if (onboarding) $("#pf-skip").addEventListener("click", () => { $("#onboarding").style.display = "none"; syncThenLoad(); });
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
    availability,
    goal_name: str("#pf-goal-name"), goal_date: str("#pf-goal-date"),
    goal_kind: str("#pf-goal-kind"), goal_priority: $("#pf-goal-prio").value || "A",
  };
  const btn = $("#pf-save"); btn.disabled = true;
  try {
    await api("/api/profile", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    $("#onboarding").style.display = "none";
    horizonLoaded = false;
    if (onboarding) { syncThenLoad(); }
    else { loadHome(); renderSettings(); }
  } catch (e) { $("#ob-msg").textContent = e.detail || "No se pudo guardar."; btn.disabled = false; }
}

// --- Chat -------------------------------------------------------------------
function addMsg(text, cls) {
  const el = document.createElement("div");
  el.className = "msg " + cls; el.textContent = text;
  $("#chat-log").appendChild(el);
  el.scrollIntoView({ behavior: "smooth", block: "end" });
}
async function sendChat() {
  const input = $("#chat-text"), msg = input.value.trim();
  if (!msg) return;
  addMsg(msg, "user"); input.value = "";
  const pending = document.createElement("div");
  pending.className = "msg bot"; pending.textContent = "…";
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
  catch (e) { $("#home-content").innerHTML = `<div class="loading">${e.detail || "Error cargando el estado."}</div>`; }
}
async function loadHorizon() {
  if (horizonLoaded) return;
  $("#horizon-content").innerHTML = SKELETON.rows;
  try { renderHorizon(await api("/api/horizon?days=7")); horizonLoaded = true; }
  catch (e) { $("#horizon-content").innerHTML = `<div class="loading">${e.detail || "Error."}</div>`; }
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
