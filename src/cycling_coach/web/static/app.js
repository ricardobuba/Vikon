// Vikon — frontend mobile-first. Consume la API JSON; el motor decide, aquí solo pintamos.
const $ = (s) => document.querySelector(s);
const api = (p, opts) => fetch(p, opts).then((r) => r.ok ? r.json() : r.json().then((e) => Promise.reject(e)));
const fmt = (v, d = 0) => (v == null ? "—" : Number(v).toFixed(d));
const signed = (v) => (v == null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(0));
const shortDate = (iso) => new Date(iso).toLocaleDateString("es-ES", { day: "numeric", month: "short" });

// --- Iconos SVG minimalistas (línea, heredan el color del texto) -------------
const ICON = {
  home: '<path d="M3 10.8 12 3.5l9 7.3"/><path d="M5.5 9.5V20h13V9.5"/>',
  chart: '<path d="M5 20V11M12 20V4.5M19 20v-6.5"/>',
  calendar: '<rect x="4" y="5" width="16" height="15" rx="2.2"/><path d="M4 9.5h16M8.5 3v4M15.5 3v4"/>',
  chat: '<path d="M20 14.5a2 2 0 0 1-2 2H8l-4 3.5V6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2z"/>',
  settings: '<path d="M3.5 7.5h9M17 7.5h3.5M3.5 16.5h3M11 16.5h9.5"/><circle cx="14.5" cy="7.5" r="2.1"/><circle cx="7.5" cy="16.5" r="2.1"/>',
  bike: '<circle cx="6" cy="16" r="3.3"/><circle cx="18" cy="16" r="3.3"/><path d="M6 16l4.2-7H14l3.6 7M9.5 9h5.2l-1.7 3.4"/>',
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
    return `<line x1="${mL}" y1="${y}" x2="${W - mR}" y2="${y}" stroke="#ffffff10"/>`
      + `<text x="${mL - 5}" y="${(+y + 3).toFixed(1)}" fill="var(--muted)" font-size="9" text-anchor="end">${Math.round(v)}</text>`;
  }).join("");
  if (cp) {
    const y = Y(cp).toFixed(1);
    grid += `<line x1="${mL}" y1="${y}" x2="${W - mR}" y2="${y}" stroke="#00D1B2" stroke-width="1" stroke-dasharray="2 3" opacity=".7"/>`
      + `<text x="${W - mR}" y="${(+y - 4).toFixed(1)}" fill="#00D1B2" font-size="9" text-anchor="end">CP ${Math.round(cp)}W</text>`;
  }
  const ticksX = [[5, "5s"], [30, "30s"], [60, "1m"], [300, "5m"], [1200, "20m"], [3600, "1h"]];
  const xlab = ticksX.filter(([s]) => s >= Math.min(...secs) && s <= Math.max(...secs)).map(([s, t]) =>
    `<line x1="${X(s).toFixed(1)}" y1="${mT}" x2="${X(s).toFixed(1)}" y2="${mT + ih}" stroke="#ffffff08"/>`
    + `<text x="${X(s).toFixed(1)}" y="${H - 8}" fill="var(--muted)" font-size="9" text-anchor="middle">${t}</text>`).join("");
  const line = (key, color, dash) => {
    const pts = points.filter((p) => p[key] != null).map((p) => `${X(p.seconds).toFixed(1)},${Y(p[key]).toFixed(1)}`);
    if (pts.length < 2) return "";
    return `<path d="M${pts.join(" L")}" fill="none" stroke="${color}" stroke-width="2.2" stroke-linejoin="round" ${dash ? 'stroke-dasharray="5 3"' : ""}/>`;
  };
  const dots = points.filter((p) => p.actual != null).map((p) =>
    `<circle cx="${X(p.seconds).toFixed(1)}" cy="${Y(p.actual).toFixed(1)}" r="2.6" fill="#2BC4FF"/>`).join("");

  el.innerHTML =
    `<div class="chart-wrap">
      <svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto;display:block">
        ${grid}${xlab}${line("predicted", "#2E7DFF", true)}${line("actual", "#2BC4FF", false)}${dots}
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
    cross.innerHTML = `<line x1="${cx}" y1="${mT}" x2="${cx}" y2="${mT + ih}" stroke="#ffffff55"/>`
      + (p.actual != null ? `<circle cx="${cx}" cy="${Y(p.actual).toFixed(1)}" r="3.5" fill="#2BC4FF" stroke="#0b0e14" stroke-width="1.5"/>` : "")
      + (p.predicted != null ? `<circle cx="${cx}" cy="${Y(p.predicted).toFixed(1)}" r="3.5" fill="#2E7DFF" stroke="#0b0e14" stroke-width="1.5"/>` : "");
    tip.style.display = "block";
    tip.style.left = Math.max(15, Math.min(85, X(p.seconds) / W * 100)) + "%";
    tip.innerHTML = `<b>${fmtDur(p.seconds)}</b>`
      + (p.actual != null ? `<span><i style="background:#2BC4FF"></i>real ${Math.round(p.actual)} W</span>` : "")
      + (p.predicted != null ? `<span><i style="background:#2E7DFF"></i>modelo ${Math.round(p.predicted)} W</span>` : "");
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
    return `<line x1="${mL}" y1="${y}" x2="${W - mR}" y2="${y}" stroke="#ffffff10"/>`
      + `<text x="${mL - 5}" y="${(+y + 3).toFixed(1)}" fill="var(--muted)" font-size="9" text-anchor="end">${Math.round(v)}${yunit}</text>`;
  }).join("");
  if (zeroLine && min < 0 && max > 0) {
    const zy = Y(0).toFixed(1);
    grid += `<line x1="${mL}" y1="${zy}" x2="${W - mR}" y2="${zy}" stroke="#ffffff40" stroke-dasharray="3 3"/>`;
  }
  // Línea "HOY" (frontera pasado/futuro)
  if (splitIndex != null && splitIndex < n - 1) {
    const sx = X(splitIndex).toFixed(1);
    grid += `<line x1="${sx}" y1="${mT}" x2="${sx}" y2="${mT + ih}" stroke="#ffffff33" stroke-dasharray="2 3"/>`
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
    cross.innerHTML = `<line x1="${cx}" y1="${mT}" x2="${cx}" y2="${mT + ih}" stroke="#ffffff55"/>`
      + series.map((s) => s.vals[i] == null ? "" :
        `<circle cx="${cx}" cy="${Y(s.vals[i]).toFixed(1)}" r="3" fill="${s.color}" stroke="#0b0e14" stroke-width="1.5"/>`).join("");
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
    ? `<div class="pill-ok"><span class="pill-ic">${icon("check", 18)}</span>Ya entrenaste hoy · ${s.trained_minutes} min</div>` : "";
  const planLabel = s.trained_today ? "Mañana" : "Plan de hoy";
  let planHtml = `<div class="loading">No hay plan. Falta el FTP.</div>`;
  if (p) {
    const adjust = p.aspired ? `<div class="badge-adjust">rebajado desde ${p.aspired}</div>` : "";
    const blocks = (p.targets || []).map(blockHtml).join("");
    planHtml = `${trained}
      <div class="hero"><div class="hero-inner">
        <div class="label">${planLabel}</div>
        <div class="session">${p.session}</div>
        <div class="objective">${p.objective.replace("_", " ")} · <span class="duration">${p.minutes} min</span></div>
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
    ${p ? `<div class="rationale">${p.rationale}</div>` : ""}`;
  // gráfica de forma + predicción (interactiva)
  api("/api/form-forecast?past=60&future=7").then((t) => {
    if (!t.length) { $("#form-svg").innerHTML = `<div class="sub">sin datos</div>`; return; }
    let split = t.findIndex((d) => d.projected);
    split = split < 0 ? t.length - 1 : split - 1;
    mountChart($("#form-svg"), {
      dates: labelDates(t.map((d) => d.day)),
      series: [
        { vals: t.map((d) => d.ctl), color: "#2BC4FF", name: "Fitness (CTL)", fill: true },
        { vals: t.map((d) => d.tsb), color: "#2E7DFF", name: "Forma (TSB)" },
      ],
      zeroLine: true, splitIndex: split,
    });
  }).catch(() => { $("#form-svg").innerHTML = `<div class="sub">—</div>`; });
}

// --- Pantalla PROGRESO ------------------------------------------------------
async function renderProgress() {
  const box = $("#progress-content");
  box.innerHTML = `<div class="loading">Cargando progreso…</div>`;
  const [ftp, pc] = await Promise.all([
    api("/api/ftp").catch(() => []),
    api("/api/power-curve").catch(() => null),
  ]);
  let html = "";
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
      ${legend([{ color: "#2BC4FF", label: "Real (potencia máx.)" }, { color: "#2E7DFF", label: "Modelo CP/W'" }])}
      ${pc.verdict ? `<div class="verdict ${pc.coherent ? "ok" : "warn"}">${pc.verdict}</div>` : ""}</div>`;
  }
  box.innerHTML = html || `<div class="loading">Sin datos de potencia todavía.</div>`;
  if (pc) mountPowerChart($("#pc-chart"), pc.points, pc.cp);
}

function renderHorizon(days) {
  if (!days.length) { $("#horizon-content").innerHTML = `<div class="loading">Sin datos.</div>`; return; }
  const names = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];
  $("#horizon-content").innerHTML = days.map((h, i) => {
    const d = i === 0 ? "HOY" : names[new Date(h.day).getDay()];
    const detail = (h.targets && h.targets.length)
      ? h.targets.map(blockHtml).join("")
      : `<div class="empty">${h.objective === "rest" ? "Descanso — sin sesión." : "Sin bloques."}</div>`;
    return `<div class="hitem">
      <div class="hrow"><span class="d">${d}</span>
        <span class="o">${h.objective.replace("_", " ")}<br><span style="color:var(--muted);font-size:12px">${h.session}</span></span>
        <span class="t">TSB ${signed(h.tsb)}<br>${h.tss} TSS</span>
        <span class="chev">${icon("chevron", 14)}</span></div>
      <div class="hdetail" style="display:none">${detail}</div>
    </div>`;
  }).join("");
  $("#horizon-content").querySelectorAll(".hrow").forEach((row) => {
    row.addEventListener("click", () => {
      const det = row.parentElement.querySelector(".hdetail");
      const open = det.style.display !== "none";
      det.style.display = open ? "none" : "block";
      row.classList.toggle("open", !open);
    });
  });
}

// --- Pantalla AJUSTES -------------------------------------------------------
async function renderSettings() {
  const box = $("#settings-content");
  box.innerHTML = `<div class="loading">Cargando ajustes…</div>`;
  let s;
  try { s = await api("/api/settings"); }
  catch (e) { box.innerHTML = `<div class="loading">${e.detail || "Error cargando ajustes."}</div>`; return; }

  const wp = s.w_prime != null ? (s.w_prime / 1000).toFixed(1) + " kJ" : "—";
  const goal = s.goal
    ? `<div class="setrow"><span>${s.goal.name || "Evento"}</span><span class="v">${shortDate(s.goal.date)} · faltan ${s.goal.days_to} d</span></div>`
    : `<div class="sub">Sin objetivo. Añade uno para activar la periodización (fase/taper).</div>`;
  const llmHost = (() => { try { return new URL(s.llm.base_url).host; } catch { return s.llm.base_url; } })();

  box.innerHTML = `
    <div class="card"><h3>Perfil y disponibilidad</h3>
      <div class="sub">Tus datos y cuánto tiempo tienes cada día. El plan encaja las sesiones en tu disponibilidad.</div>
      <button id="edit-profile" class="btn-full" style="margin-top:10px">Editar perfil y disponibilidad</button>
    </div>

    <div class="card"><h3>Tu motor</h3>
      <div class="setrow"><span>FTP</span><span class="v">${fmt(s.ftp)} W</span></div>
      <div class="setrow"><span>CP (potencia crítica)</span><span class="v">${fmt(s.cp)} W</span></div>
      <div class="setrow"><span>W′ (reserva anaeróbica)</span><span class="v">${wp}</span></div>
      <div class="sub" style="margin-top:8px">Se recalcula con <code>cc estimate-cp</code> tras un test o esfuerzo máximo.</div>
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
      <div class="setrow"><span>Actividades importadas</span><span class="v">${s.activities}</span></div>
      <div class="setrow"><span>Última actividad</span><span class="v">${s.last_activity ? shortDate(s.last_activity) : "—"}</span></div>
      <div class="sub" style="margin:8px 0">Los entrenamientos entran solos desde Strava. Puedes forzar una sincronización ahora:</div>
      <button id="sync-now" class="btn-full">${icon("refresh", 18)} Sincronizar con Strava</button>
      <div id="sync-msg" class="sub"></div>
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
      loadHome(); horizonLoaded = false;
      renderSettings();
    } catch (e) { $("#goal-msg").textContent = e.detail || "No se pudo guardar."; }
  });

  $("#sync-now").addEventListener("click", async (ev) => {
    const btn = ev.currentTarget; btn.disabled = true;
    $("#sync-msg").textContent = "Sincronizando con Strava…";
    try {
      const r = await api("/api/sync", { method: "POST" });
      $("#sync-msg").textContent = r.new > 0
        ? `✓ ${r.new} nueva(s) actividad(es) importada(s).`
        : "✓ Ya estaba todo al día.";
      if (r.new > 0) { loadHome(); horizonLoaded = false; renderSettings(); }
    } catch (e) { $("#sync-msg").textContent = e.detail || "No se pudo sincronizar (¿credenciales de Strava?)."; }
    finally { btn.disabled = false; }
  });
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
  boot();
}

// --- Perfil / onboarding ----------------------------------------------------
const DAYS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"];

function profileFormHtml(p, onboarding) {
  const v = (x) => (x == null ? "" : x);
  const av = p.availability || {};
  const days = DAYS.map((d, i) =>
    `<div class="row"><span class="day">${d}</span>
      <input type="number" min="0" max="600" step="5" id="av-${i}" value="${av[i] != null ? av[i] : ""}" placeholder="0" />
      <span class="u">min</span></div>`).join("");
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
    <div class="avail-total">Total semanal: <b id="av-sum">0</b> min
      · objetivo <input type="number" id="wk-target" value="${v(p.weekly_minutes_target)}" placeholder="—"
        style="width:70px;display:inline-block;padding:6px 8px" /> min</div>

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
    for (let i = 0; i < 7; i++) sum += Number($(`#av-${i}`).value || 0);
    $("#av-sum").textContent = sum;
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
    weekly_minutes_target: num("#wk-target"), availability,
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
  try {
    const r = await api("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: msg }) });
    pending.remove();
    const logged = Object.keys(r.logged || {});
    if (logged.length) addMsg("✓ registrado: " + logged.join(", "), "hint");
    const hint = [];
    if (r.intent.minutes != null) hint.push(r.intent.minutes + " min");
    if (r.intent.readiness) hint.push(r.intent.readiness);
    if (hint.length) addMsg("interpretado: " + hint.join(", "), "hint");
    addMsg(r.text, "bot");
    loadHome();
  } catch (e) { pending.remove(); addMsg(e.detail || "No pude responder (¿LLM configurado?).", "bot"); }
}

// --- Navegación -------------------------------------------------------------
function show(view) {
  ["home", "progress", "horizon", "chat", "settings"].forEach((v) => { $(`#${v}-view`).style.display = v === view ? "block" : "none"; });
  document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  const isChat = view === "chat";
  $("#chat-input").style.display = isChat ? "flex" : "none";
  $("#quick").style.display = isChat ? "flex" : "none";
  if (isChat) $("#chat-text").focus();
  if (view === "horizon") loadHorizon();
  if (view === "progress") renderProgress();
  if (view === "settings") renderSettings();
}

let horizonLoaded = false;
async function loadHome() {
  try { renderHome(await api("/api/state")); }
  catch (e) { $("#home-content").innerHTML = `<div class="loading">${e.detail || "Error cargando el estado."}</div>`; }
}
async function loadHorizon() {
  if (horizonLoaded) return;
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
    if (r.new > 0) { horizonLoaded = false; loadHome(); }   // datos nuevos → refresca
  } catch (_) { /* sin conexión/credenciales/timeout: seguimos con lo cargado */ }
}

// --- Init -------------------------------------------------------------------
paintIcons();
$("#today").textContent = new Date().toLocaleDateString("es-ES", { weekday: "long", day: "numeric", month: "short" });
document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));
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
}
boot();
