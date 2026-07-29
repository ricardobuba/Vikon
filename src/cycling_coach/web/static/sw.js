/* Service worker de Vikon.
 * Estrategia deliberada: NADA de cachear la API (los datos de entrenamiento
 * deben ser siempre frescos; una respuesta vieja daría un plan equivocado).
 * Solo cachea el "armazón" (HTML/CSS/JS/iconos) para que la app abra al
 * instante y muestre algo aunque no haya red. */
const CACHE = "vikon-shell-v21";
const SHELL = [
  "/", "/static/styles.css?v=21", "/static/app.js?v=21",
  "/static/logo.png", "/static/isotipo.png", "/static/manifest.webmanifest",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.pathname.startsWith("/api/")) return;  // API: siempre red
  e.respondWith(
    fetch(e.request)                       // red primero: el código nuevo manda
      .then((r) => {
        if (r.ok && url.origin === self.location.origin) {
          const copy = r.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return r;
      })
      .catch(() => caches.match(e.request))  // sin red: lo último que se vio
  );
});
