// Vikon — frontend mobile-first. Consume la API JSON; el motor decide, aquí solo pintamos.
const $ = (s) => document.querySelector(s);
const api = (p, opts) => fetch(p, opts).then((r) => r.ok ? r.json() : r.json().then((e) => Promise.reject(e)));
const fmt = (v, d = 0) => (v == null ? "—" : Number(v).toFixed(d));
const signed = (v) => (v == null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(0));
const shortDate = (iso) => new Date(iso).toLocaleDateString("es-ES", { day: "numeric", month: "short" });

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

// Gráfica de líneas con EJES ETIQUETADOS. series: [{vals,color,fill,dash}].
// opts: {h, zeroLine, yfmt, xlabels:[{i,text}], ylabel}
function chart(series, opts = {}) {
  const W = 340, H = opts.h || 168;
  const mL = 38, mR = 10, mT = 10, mB = 22;
  const iw = W - mL - mR, ih = H - mT - mB;
  const all = series.flatMap((s) => s.vals).filter((v) => v != null);
  if (all.length < 2) return `<div class="sub">sin datos suficientes</div>`;
  let min = Math.min(...all), max = Math.max(...all);
  if (min === max) { min -= 1; max += 1; }
  const pad = (max - min) * 0.12; min -= pad; max += pad;
  const n = Math.max(...series.map((s) => s.vals.length));
  const X = (i) => mL + (n <= 1 ? iw / 2 : i / (n - 1) * iw);
  const Y = (v) => mT + (1 - (v - min) / (max - min)) * ih;
  const yfmt = opts.yfmt || ((v) => Math.round(v));

  // rejilla + etiquetas del eje Y
  let grid = niceTicks(min, max, 4).map((v) => {
    const y = Y(v).toFixed(1);
    return `<line x1="${mL}" y1="${y}" x2="${W - mR}" y2="${y}" stroke="#ffffff10"/>`
      + `<text x="${mL - 5}" y="${(+y + 3).toFixed(1)}" fill="var(--muted)" font-size="9" text-anchor="end">${yfmt(v)}</text>`;
  }).join("");
  if (opts.zeroLine && min < 0 && max > 0) {
    const zy = Y(0).toFixed(1);
    grid += `<line x1="${mL}" y1="${zy}" x2="${W - mR}" y2="${zy}" stroke="#ffffff40" stroke-dasharray="3 3"/>`;
  }

  const paths = series.map((s) => {
    const pts = s.vals.map((v, i) => v == null ? null : `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).filter(Boolean);
    if (pts.length < 2) return "";
    const d = "M" + pts.join(" L");
    const base = (mT + ih).toFixed(1);
    const area = s.fill ? `<path d="${d} L${X(s.vals.length - 1).toFixed(1)},${base} L${X(0).toFixed(1)},${base} Z" fill="${s.color}" opacity="0.10"/>` : "";
    const dash = s.dash ? `stroke-dasharray="4 3"` : "";
    return `${area}<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round" ${dash}/>`;
  }).join("");

  const xlab = (opts.xlabels || []).map((t) =>
    `<text x="${X(t.i).toFixed(1)}" y="${H - 6}" fill="var(--muted)" font-size="9" text-anchor="middle">${t.text}</text>`
  ).join("");

  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto">${grid}${paths}${xlab}</svg>`;
}

// Tres marcas de fecha (inicio · medio · fin) para una serie diaria. Si la serie
// abarca varios años (p. ej. la evolución del FTP), muestra el año para no
// confundir (ordena por fecha real, no por día/mes suelto).
function dateTicks(days) {
  const n = days.length;
  if (!n) return [];
  const spanYears = new Date(days[0]).getFullYear() !== new Date(days[n - 1]).getFullYear();
  const label = (iso) => spanYears
    ? new Date(iso).toLocaleDateString("es-ES", { month: "short", year: "2-digit" })
    : shortDate(iso);
  return [0, Math.floor(n / 2), n - 1].map((i) => ({ i, text: label(days[i]) }));
}

const legend = (items) => `<div class="legend">${items.map((i) =>
  `<span><i class="dot" style="background:${i.color}"></i>${i.label}</span>`).join("")}</div>`;

// Curva de potencia: eje X logarítmico (5 s → 1 h), real vs modelo CP/W'.
function powerChart(points, cp) {
  const W = 340, H = 196;
  const mL = 40, mR = 12, mT = 10, mB = 28;
  const iw = W - mL - mR, ih = H - mT - mB;
  const lx = (s) => Math.log10(s);
  const secs = points.map((p) => p.seconds);
  const xmin = lx(Math.min(...secs)), xmax = lx(Math.max(...secs));
  const X = (s) => mL + (lx(s) - xmin) / (xmax - xmin) * iw;
  const yv = points.flatMap((p) => [p.actual, p.predicted]).filter((v) => v != null);
  if (!yv.length) return `<div class="sub">sin datos</div>`;
  const ymax = Math.max(...yv) * 1.05;
  const Y = (v) => mT + (1 - v / ymax) * ih;

  let grid = niceTicks(0, ymax, 4).map((v) => {
    const y = Y(v).toFixed(1);
    return `<line x1="${mL}" y1="${y}" x2="${W - mR}" y2="${y}" stroke="#ffffff10"/>`
      + `<text x="${mL - 5}" y="${(+y + 3).toFixed(1)}" fill="var(--muted)" font-size="9" text-anchor="end">${Math.round(v)}</text>`;
  }).join("");
  // línea del CP (asíntota aeróbica)
  if (cp) {
    const y = Y(cp).toFixed(1);
    grid += `<line x1="${mL}" y1="${y}" x2="${W - mR}" y2="${y}" stroke="#00D1B2" stroke-width="1" stroke-dasharray="2 3" opacity=".7"/>`
      + `<text x="${W - mR}" y="${(+y - 4).toFixed(1)}" fill="#00D1B2" font-size="9" text-anchor="end">CP ${Math.round(cp)}W</text>`;
  }

  const ticksX = [[5, "5s"], [30, "30s"], [60, "1m"], [300, "5m"], [1200, "20m"], [3600, "1h"]];
  const xlab = ticksX.filter(([s]) => s >= Math.min(...secs) && s <= Math.max(...secs)).map(([s, t]) =>
    `<line x1="${X(s).toFixed(1)}" y1="${mT}" x2="${X(s).toFixed(1)}" y2="${mT + ih}" stroke="#ffffff08"/>`
    + `<text x="${X(s).toFixed(1)}" y="${H - 8}" fill="var(--muted)" font-size="9" text-anchor="middle">${t}</text>`
  ).join("");

  const line = (key, color, dash) => {
    const pts = points.filter((p) => p[key] != null).map((p) => `${X(p.seconds).toFixed(1)},${Y(p[key]).toFixed(1)}`);
    if (pts.length < 2) return "";
    return `<path d="M${pts.join(" L")}" fill="none" stroke="${color}" stroke-width="2.2" stroke-linejoin="round" ${dash ? 'stroke-dasharray="5 3"' : ""}/>`;
  };
  const dots = points.filter((p) => p.actual != null).map((p) =>
    `<circle cx="${X(p.seconds).toFixed(1)}" cy="${Y(p.actual).toFixed(1)}" r="2.6" fill="#2BC4FF"/>`).join("");

  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:auto">${grid}${xlab}`
    + `${line("predicted", "#2E7DFF", true)}${line("actual", "#2BC4FF", false)}${dots}</svg>`;
}

// --- Pantalla HOY -----------------------------------------------------------
function renderHome(s) {
  const p = s.plan;
  const tsbClass = s.tsb == null ? "" : s.tsb >= 0 ? "pos" : "neg";
  let goal = "";
  if (s.goal_date) goal = `<div class="goal-pill">🎯 ${s.goal_name || "Evento"} · faltan ${s.days_to_event} d · fase ${s.phase}</div>`;
  const trained = s.trained_today
    ? `<div class="goal-pill" style="background:rgba(0,209,178,.12);border-color:rgba(0,209,178,.4)">✅ Ya entrenaste hoy (${s.trained_minutes} min)</div>` : "";
  const planLabel = s.trained_today ? "Mañana" : "Plan de hoy";
  let planHtml = `<div class="loading">No hay plan. Falta el FTP.</div>`;
  if (p) {
    const adjust = p.aspired ? `<div class="badge-adjust">rebajado desde ${p.aspired}</div>` : "";
    const blocks = (p.targets || []).map((t) => {
      const m = t.match(/(\d[\d–\-]*\s*W)/);
      const w = m ? m[1] : "", label = w ? t.replace(w, "").trim() : t;
      return `<div class="block"><span>${label}</span><span class="w">${w}</span></div>`;
    }).join("");
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
    ${goal}
    <div class="card" id="form-chart"><h3>Forma · últimos 90 días</h3><div class="sub">Fitness (CTL) y frescura (TSB)</div><div id="form-svg" class="loading" style="padding:20px">…</div></div>
    ${p ? `<div class="rationale">${p.rationale}</div>` : ""}`;
  // gráfica de forma (async)
  api("/api/trend?days=90").then((t) => {
    if (!t.length) { $("#form-svg").innerHTML = `<div class="sub">sin datos</div>`; return; }
    $("#form-svg").outerHTML = chart([
      { vals: t.map((d) => d.ctl), color: "#2BC4FF", fill: true },
      { vals: t.map((d) => d.tsb), color: "#2E7DFF" },
    ], { zeroLine: true, xlabels: dateTicks(t.map((d) => d.day)) }) + legend([
      { color: "#2BC4FF", label: "Fitness (CTL)" }, { color: "#2E7DFF", label: "Forma (TSB)" },
    ]);
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
  // Evolución FTP / CP
  if (ftp.length) {
    const cur = ftp[ftp.length - 1];
    html += `<div class="card"><h3>Evolución de tu motor</h3>
      <div class="sub">FTP ${cur.ftp} W · CP ${cur.cp} W</div>
      ${chart([
        { vals: ftp.map((d) => d.ftp), color: "#2E7DFF", fill: true },
        { vals: ftp.map((d) => d.cp), color: "#2BC4FF" },
      ], { h: 150, yfmt: (v) => Math.round(v) + "W", xlabels: dateTicks(ftp.map((d) => d.day)) })}
      ${legend([{ color: "#2E7DFF", label: "FTP" }, { color: "#2BC4FF", label: "CP" }])}</div>`;
  }
  // Curva de potencia (real vs modelo) + coherencia del CP
  if (pc) {
    html += `<div class="card"><h3>Curva de potencia</h3>
      <div class="sub">Tu mejor real (120 d) vs modelo CP/W'</div>
      ${powerChart(pc.points, pc.cp)}
      ${legend([{ color: "#2BC4FF", label: "Real (potencia máx.)" }, { color: "#2E7DFF", label: "Modelo CP/W'" }])}
      ${pc.verdict ? `<div class="verdict ${pc.coherent ? "ok" : "warn"}">${pc.verdict}</div>` : ""}</div>`;
  }
  box.innerHTML = html || `<div class="loading">Sin datos de potencia todavía.</div>`;
}

function renderHorizon(days) {
  if (!days.length) { $("#horizon-content").innerHTML = `<div class="loading">Sin datos.</div>`; return; }
  const names = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];
  $("#horizon-content").innerHTML = days.map((h, i) => {
    const d = i === 0 ? "HOY" : names[new Date(h.day).getDay()];
    return `<div class="hrow"><span class="d">${d}</span>
      <span class="o">${h.objective.replace("_", " ")}<br><span style="color:var(--muted);font-size:12px">${h.session}</span></span>
      <span class="t">TSB ${signed(h.tsb)}<br>${h.tss} TSS</span>
      <span class="bar" style="opacity:${0.3 + Math.min(h.tss, 120) / 120 * 0.7}"></span></div>`;
  }).join("");
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
        <input id="goal-name" placeholder="Nombre (p. ej. Gran Fondo)" autocomplete="off" />
        <input id="goal-date" type="date" />
        <select id="goal-prio"><option value="A">A · principal</option><option value="B">B</option><option value="C">C</option></select>
        <button id="goal-save">Guardar objetivo</button>
      </div>
      <div id="goal-msg" class="sub"></div>
    </div>

    <div class="card"><h3>Datos</h3>
      <div class="setrow"><span>Actividades importadas</span><span class="v">${s.activities}</span></div>
      <div class="setrow"><span>Última actividad</span><span class="v">${s.last_activity ? shortDate(s.last_activity) : "—"}</span></div>
      <div class="sub" style="margin:8px 0">Los entrenamientos entran solos desde Strava. Puedes forzar una sincronización ahora:</div>
      <button id="sync-now" class="btn-full">↻ Sincronizar con Strava</button>
      <div id="sync-msg" class="sub"></div>
    </div>

    <div class="card"><h3>Vikon IA</h3>
      <div class="setrow"><span>Estado</span><span class="v">${s.llm.configured ? "✅ conectada" : "⚠️ sin clave"}</span></div>
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
        body: JSON.stringify({ name: $("#goal-name").value || null, date, priority: $("#goal-prio").value }),
      });
      $("#goal-msg").textContent = `✓ Objetivo guardado (faltan ${r.days_to} días).`;
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
