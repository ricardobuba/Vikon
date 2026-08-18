/* Service worker de Vikon.
 * Estrategia deliberada: NADA de cachear la API (los datos de entrenamiento
 * deben ser siempre frescos; una respuesta vieja daría un plan equivocado).
 * Solo cachea el "armazón" (HTML/CSS/JS/iconos) para que la app abra al
 * instante y muestre algo aunque no haya red.
 * OJO: al cambiar assets hay que subir la versión AQUÍ y en index.html. */
const CACHE = "vikon-shell-v27";
const SHELL = [
  "/", "/static/tokens.css?v=27", "/static/base.css?v=27", "/static/app.js?v=27",
  "/static/fonts.css", "/static/logo.png", "/static/manifest.webmanifest",
  "/static/icon-192.png?v=27", "/static/favicon.ico?v=27",
  // Las tipografías van en el armazón: autoalojadas y cacheadas, la app abre
  // con su identidad aunque no haya red (y sin pedirle nada a Google).
  "/static/fonts/space-grotesk-var-latin.woff2",
  "/static/fonts/inter-var-latin.woff2",
  "/static/fonts/ibm-plex-mono-400-latin.woff2",
  "/static/fonts/ibm-plex-mono-500-latin.woff2",
  "/static/fonts/ibm-plex-mono-600-latin.woff2",
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
