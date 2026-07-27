// Vikon — frontend mobile-first. Consume la API JSON; el motor decide, aquí solo pintamos.
const $ = (s) => document.querySelector(s);
const api = (p, opts) => fetch(p, opts).then((r) => r.ok ? r.json() : r.json().then((e) => Promise.reject(e)));
const fmt = (v, d = 0) => (v == null ? "—" : Number(v).toFixed(d));
const signed = (v) => (v == null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(0));

// --- Gráfica SVG de líneas (sin dependencias) -------------------------------
function sparkPath(vals, w, h, min, max) {
  if (vals.length < 2) return "";
  const dx = w / (vals.length - 1), sp = max - min || 1;
  return vals.map((v, i) => `${i ? "L" : "M"}${(i * dx).toFixed(1)} ${(h - (v - min) / sp * h).toFixed(1)}`).join(" ");
}
function multiLine(series, { w = 320, h = 130, height = 130, zeroLine = false } = {}) {
  const all = series.flatMap((s) => s.vals).filter((v) => v != null);
  if (!all.length) return `<div class="sub">sin datos</div>`;
  let min = Math.min(...all), max = Math.max(...all);
  if (min === max) { min -= 1; max += 1; }
  const pad = (max - min) * 0.12; min -= pad; max += pad;
  let extra = "";
  if (zeroLine && min < 0 && max > 0) {
    const zy = (h - (0 - min) / (max - min) * h).toFixed(1);
    extra = `<line x1="0" y1="${zy}" x2="${w}" y2="${zy}" stroke="#ffffff22" stroke-dasharray="3 3" vector-effect="non-scaling-stroke"/>`;
  }
  const paths = series.map((s) => {
    const d = sparkPath(s.vals, w, h, min, max);
    const area = s.fill ? `<path d="${d} L${w} ${h} L0 ${h} Z" fill="${s.color}" opacity="0.10"/>` : "";
    return `${area}<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2.2" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>`;
  }).join("");
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="width:100%;height:${height}px">${extra}${paths}</svg>`;
}
const legend = (items) => `<div class="legend">${items.map((i) => `<span><i class="dot" style="background:${i.color}"></i>${i.label}</span>`).join("")}</div>`;

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
    $("#form-svg").outerHTML = multiLine([
      { vals: t.map((d) => d.ctl), color: "#2BC4FF", fill: true },
      { vals: t.map((d) => d.tsb), color: "#2E7DFF" },
    ], { zeroLine: true }) + legend([
      { color: "#2BC4FF", label: "Fitness (CTL)" }, { color: "#2E7DFF", label: "Forma (TSB)" },
    ]);
  }).catch(() => { $("#form-svg").innerHTML = `<div class="sub">—</div>`; });
}

// --- Pantalla PROGRESO ------------------------------------------------------
async function renderProgress() {
  const box = $("#progress-content");
  box.innerHTML = `<div class="loading">Cargando progreso…</div>`;
  const [ftp, coh] = await Promise.all([
    api("/api/ftp").catch(() => []),
    api("/api/coherence").catch(() => null),
  ]);
  let html = "";
  // Evolución FTP / CP
  if (ftp.length) {
    const cur = ftp[ftp.length - 1];
    html += `<div class="card"><h3>Evolución de tu motor</h3>
      <div class="sub">FTP ${cur.ftp} W · CP ${cur.cp} W</div>
      ${multiLine([
        { vals: ftp.map((d) => d.ftp), color: "#2E7DFF", fill: true },
        { vals: ftp.map((d) => d.cp), color: "#2BC4FF" },
      ], { height: 150 })}
      ${legend([{ color: "#2E7DFF", label: "FTP" }, { color: "#2BC4FF", label: "CP" }])}</div>`;
  }
  // Curva de potencia + coherencia del CP
  if (coh) {
    const maxv = Math.max(...coh.checks.map((c) => Math.max(c.actual || 0, c.predicted)));
    const bars = coh.checks.filter((c) => c.seconds >= 60).map((c) => {
      const dur = c.seconds >= 60 ? `${c.seconds / 60}min` : `${c.seconds}s`;
      const wpct = ((c.actual || 0) / maxv * 100).toFixed(0);
      return `<div class="pcbar ${c.exceeds ? "exceed" : ""}">
        <span class="lbl">${dur}</span>
        <span class="track"><span class="fill" style="width:${wpct}%"></span></span>
        <span class="val">${c.actual ? Math.round(c.actual) : "—"}</span></div>`;
    }).join("");
    html += `<div class="card"><h3>Curva de potencia</h3>
      <div class="sub">Tu mejor real (120 d) vs modelo · CP ${Math.round(coh.cp)} W</div>
      ${bars}
      <div class="verdict ${coh.coherent ? "ok" : "warn"}">${coh.verdict}</div></div>`;
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
  ["home", "progress", "horizon", "chat"].forEach((v) => { $(`#${v}-view`).style.display = v === view ? "block" : "none"; });
  document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  const isChat = view === "chat";
  $("#chat-input").style.display = isChat ? "flex" : "none";
  $("#quick").style.display = isChat ? "flex" : "none";
  if (isChat) $("#chat-text").focus();
  if (view === "horizon") loadHorizon();
  if (view === "progress") renderProgress();
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
syncThenLoad();
