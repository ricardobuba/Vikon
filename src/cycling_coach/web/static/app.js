// Vikon — frontend mobile-first. Consume la API JSON; el motor decide, aquí solo pintamos.
const $ = (s) => document.querySelector(s);
const api = (p, opts) => fetch(p, opts).then((r) => r.ok ? r.json() : r.json().then((e) => Promise.reject(e)));

const fmt = (v, d = 0) => (v == null ? "—" : Number(v).toFixed(d));
const signed = (v) => (v == null ? "—" : (v >= 0 ? "+" : "") + v.toFixed(0));

// --- Pantalla HOY ---
function renderHome(s) {
  const p = s.plan;
  const tsbClass = s.tsb == null ? "" : s.tsb >= 0 ? "pos" : "neg";
  let goal = "";
  if (s.goal_date) {
    goal = `<div class="goal-pill">🎯 ${s.goal_name || "Evento"} · faltan ${s.days_to_event} d · fase ${s.phase}</div>`;
  }
  let planHtml = `<div class="loading">No hay plan. Falta el FTP.</div>`;
  if (p) {
    const adjust = p.aspired ? `<div class="badge-adjust">rebajado desde ${p.aspired}</div>` : "";
    const blocks = (p.targets || []).map((t) => {
      const m = t.match(/(\d[\d–\-]*\s*W)/);
      const w = m ? m[1] : "";
      const label = w ? t.replace(w, "").trim() : t;
      return `<div class="block"><span>${label}</span><span class="w">${w}</span></div>`;
    }).join("");
    planHtml = `
      <div class="hero"><div class="hero-inner">
        <div class="label">Plan de hoy</div>
        <div class="session">${p.session}</div>
        <div class="objective">${p.objective.replace("_", " ")} · <span class="duration">${p.minutes} min</span></div>
        ${adjust}
        <div class="blocks">${blocks}</div>
      </div></div>`;
  }
  $("#home-content").innerHTML = `
    ${planHtml}
    <div class="stats">
      <div class="stat ${tsbClass}"><span class="num">${signed(s.tsb)}</span><span class="k">FORMA</span></div>
      <div class="stat"><span class="num">${fmt(s.ctl)}</span><span class="k">FITNESS</span></div>
      <div class="stat"><span class="num">${fmt(s.cri)}</span><span class="k">CRI</span></div>
      <div class="stat"><span class="num">${fmt(s.ftp)}</span><span class="k">FTP W</span></div>
    </div>
    ${goal}
    ${p ? `<div class="rationale">${p.rationale}</div>` : ""}
  `;
}

function renderHorizon(days) {
  if (!days.length) { $("#horizon-content").innerHTML = `<div class="loading">Sin datos.</div>`; return; }
  const names = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];
  $("#horizon-content").innerHTML = days.map((h, i) => {
    const d = i === 0 ? "HOY" : names[new Date(h.day).getDay()];
    return `<div class="hrow">
      <span class="d">${d}</span>
      <span class="o">${h.objective.replace("_", " ")}<br><span style="color:var(--muted);font-size:12px">${h.session}</span></span>
      <span class="t">TSB ${signed(h.tsb)}<br>${h.tss} TSS</span>
      <span class="bar" style="opacity:${0.3 + Math.min(h.tss, 120) / 120 * 0.7}"></span>
    </div>`;
  }).join("");
}

// --- Chat ---
function addMsg(text, cls) {
  const el = document.createElement("div");
  el.className = "msg " + cls;
  el.textContent = text;
  $("#chat-log").appendChild(el);
  $("#chat-view").scrollIntoView(false);
  el.scrollIntoView({ behavior: "smooth", block: "end" });
}

async function sendChat() {
  const input = $("#chat-text");
  const msg = input.value.trim();
  if (!msg) return;
  addMsg(msg, "user");
  input.value = "";
  const pending = document.createElement("div");
  pending.className = "msg bot"; pending.textContent = "…";
  $("#chat-log").appendChild(pending);
  try {
    const r = await api("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: msg }),
    });
    pending.remove();
    const logged = Object.keys(r.logged || {});
    if (logged.length) addMsg("✓ registrado: " + logged.join(", "), "hint");
    const hint = [];
    if (r.intent.minutes != null) hint.push(r.intent.minutes + " min");
    if (r.intent.readiness) hint.push(r.intent.readiness);
    if (hint.length) addMsg("interpretado: " + hint.join(", "), "hint");
    addMsg(r.text, "bot");
    loadHome();  // el chat puede cambiar el plan (o registrar datos) → refresca Hoy
  } catch (e) {
    pending.remove();
    addMsg(e.detail || "No pude responder (¿LLM configurado?).", "bot");
  }
}

// --- Navegación ---
function show(view) {
  ["home", "horizon", "chat"].forEach((v) => {
    $(`#${v}-view`).style.display = v === view ? "block" : "none";
  });
  document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  $("#chat-input").style.display = view === "chat" ? "flex" : "none";
  if (view === "horizon") loadHorizon();
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

// --- Sincronización automática al abrir (trae la salida de hoy sin backfill manual) ---
async function syncThenLoad() {
  $("#home-content").innerHTML = `<div class="loading">Sincronizando con Strava…</div>`;
  try {
    const r = await api("/api/refresh", { method: "POST" });
    if (r.new > 0) horizonLoaded = false;   // hay datos nuevos → recalcular horizonte
  } catch (_) { /* sin conexión/credenciales: seguimos con lo que haya en BD */ }
  loadHome();
}

// --- Init ---
$("#today").textContent = new Date().toLocaleDateString("es-ES", { weekday: "long", day: "numeric", month: "short" });
document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));
$("#chat-send").addEventListener("click", sendChat);
$("#chat-text").addEventListener("keydown", (e) => { if (e.key === "Enter") sendChat(); });
syncThenLoad();
